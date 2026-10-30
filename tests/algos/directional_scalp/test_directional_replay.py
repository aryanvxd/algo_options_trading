"""Directional scalp replays on synthetic days: NIFTY, India VIX, the option chain, the NIFTY
future and the 50 NIFTY stocks every 15 s, with previous closes stored as a live run would."""

from __future__ import annotations

import math
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.helpers import instrument_rows, seed_day, tick
from trading_core.clock import ist
from trading_core.config import load_universe
from trading_core.db import models as m
from trading_core.db.repo import Journal
from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo

DAY, EXPIRY = date(2026, 10, 29), date(2026, 11, 3)        # Thursday, DTE 3
CAL = MarketCalendar([])
FUT_TOKEN = 30_000_001
# Synthetic ticks are 15 s apart: relax the feed and quote-age limits.
LOOSE = {"exits": {"stale_feed_seconds": 60}, "option": {"max_quote_age_s": 20}}


def minute(frac: float) -> float:
    """Minutes since 09:15 from seed_day's session fraction (09:15 = 0, 15:30 = 1)."""
    return frac * 375


def seed(db, platform, spot, stocks_pct, ce_skew=1.0, prev_closes=True):
    """``spot(m)`` and ``stocks_pct(m)`` take minutes since 09:15; every stock moves by stocks_pct %."""
    seed_day(db, platform, DAY, EXPIRY, spot=lambda f: spot(minute(f)), decay=lambda f: 1.5, ce_skew=ce_skew,
             strikes=(24400, 25600))
    symbols = [k.split(":", 1)[1] for k in load_universe(platform, "nifty50")]
    stocks = {s: 20_000_000 + i for i, s in enumerate(symbols)}
    rows = instrument_rows(EXPIRY) + [
        dict(instrument_token=tok, exchange_token=None, tradingsymbol=s, name=s, exchange="NSE", segment="NSE",
             instrument_type="EQ", strike=0, expiry="", lot_size=1, tick_size=0.05) for s, tok in stocks.items()] + [
        dict(instrument_token=FUT_TOKEN, exchange_token=None, tradingsymbol="NIFTY26NOVFUT", name="NIFTY",
             exchange="NFO", segment="NFO-FUT", instrument_type="FUT", strike=0, expiry=date(2026, 11, 24),
             lot_size=65, tick_size=0.1)]
    InstrumentStore(db, platform.instruments).save_snapshot(DAY, rows)
    journal = Journal(db)
    run = journal.find_run("recorder", DAY, "paper", "live")
    if prev_closes:
        journal.save_prev_closes(DAY, {tok: (s, 1000.0) for s, tok in stocks.items()}, ist(DAY, time(9, 15)), "test")
    t, end, ticks, vol = ist(DAY, time(9, 15)), ist(DAY, time(11, 45)), [], 0
    while t <= end:
        mins = (t - ist(DAY, time(9, 15))).total_seconds() / 60
        vol += 1000
        ticks.append(Tick(FUT_TOKEN, ltp=round(spot(mins) + 20, 2), bid=None, ask=None, ts=t, received_at=t,
                          volume=vol))
        px = round(1000 * (1 + stocks_pct(mins) / 100), 2)
        ticks += [tick(tok, ltp=px, when=t) for tok in stocks.values()]
        t += timedelta(seconds=15)
    journal.add_ticks(run.run_id, ticks)


def replay(platform, db, overrides=None):
    return run_algo(platform, db, "directional_scalp", replay_date=DAY, overrides={**LOOSE, **(overrides or {})},
                    calendar=CAL)


def positions(db):
    with db.session() as s:
        return s.scalars(select(m.Position).options(selectinload(m.Position.legs))
                         .order_by(m.Position.position_id)).all()


def events(db, type_):
    with db.session() as s:
        return s.scalars(select(m.Event).where(m.Event.type == type_).order_by(m.Event.id)).all()


def checks(db, name):
    with db.session() as s:
        return s.scalars(select(m.RunCheck).where(m.RunCheck.check_name == name).order_by(m.RunCheck.id)).all()


def trend(direction: int, start: float = 35, end: float = 95, rate: float = 2.5):
    """Flat at 25000, then ``rate`` pts/min in ``direction`` from minute ``start`` to ``end``, then flat."""
    return lambda mins: 25000 + direction * rate * (min(max(mins, start), end) - start)


def test_rising_day_buys_calls_books_lot_a_and_trails_lot_b(platform, db):
    seed(db, platform, trend(+1), lambda mins: 0.6)           # every stock +0.6%: weighted A/D +100
    replay(platform, db)
    trades = positions(db)
    first = trades[0]
    assert first.params["side"] == "CE" and first.status == "CLOSED"
    assert [l.role for l in first.legs] == ["LONG_CE_A", "LONG_CE_B"] and all(l.qty == 65 for l in first.legs)
    assert time(9, 54) <= first.entry_time.time() <= time(10, 0)      # second up brick after 09:45
    a, b = first.legs
    assert first.entry_credit_pts == -a.entry_price                   # lot A + lot B read as one option
    assert a.exit_price >= a.entry_price + 12                         # lot A booked at its target
    assert b.exit_price > a.exit_price                                # lot B ran further
    assert first.net_pnl > 0 and first.capital_source == "premium"
    [partial, *_] = events(db, "PARTIAL_EXIT")
    assert "lot A target" in partial.message
    assert len(trades) <= 5 and all(p.params["side"] == "CE" for p in trades)
    bias = checks(db, "bias")
    assert bias and bias[-1].detail["b1"] == 1.0 and bias[-1].detail["side"] == "CE"
    [trigger, *_] = [c for c in checks(db, "trigger") if c.passed]
    assert trigger.detail["bricks"] >= 2 and trigger.detail["option"].endswith("CE")


def test_falling_day_with_rich_puts_buys_puts(platform, db):
    # ce_skew 0.6: calls carry 60% of the puts' time value -> PE/CE ≈ 1.67 at ±300 -> B2 = -1
    seed(db, platform, trend(-1), lambda mins: -0.6, ce_skew=0.6)
    replay(platform, db)
    first = positions(db)[0]
    assert first.params["side"] == "PE" and first.params["bias"]["b2"] == -1.0
    assert first.legs[0].tradingsymbol.endswith("PE") and first.net_pnl > 0


def test_flat_day_never_triggers_even_with_a_bullish_bias(platform, db):
    seed(db, platform, lambda mins: 25000 + 4 * math.sin(mins / 3), lambda mins: 0.6)
    run_id = replay(platform, db)
    assert positions(db) == []
    with db.session() as s:
        run = s.get(m.AlgoRun, run_id)
    assert run.status == "SKIPPED" and "Renko brick" in run.skip_reason


def test_reversal_after_entry_is_cut_quickly(platform, db):
    # Rises into the trigger (~09:58), then drops 6 pts a minute.
    up = trend(+1)
    seed(db, platform, lambda mins: up(mins) if mins < 44 else up(44) - 6 * (mins - 44), lambda mins: 0.6)
    replay(platform, db)
    first = positions(db)[0]
    assert first.params["side"] == "CE"
    assert first.exit_reason in ("STOP_LOSS", "RENKO_REVERSAL", "BIAS_FLIP")
    assert first.gross_pnl >= -(8 + 2) * 130                          # about the hard stop, not the premium
    assert (first.exit_time - first.entry_time) < timedelta(minutes=10)


def test_no_previous_closes_means_no_breadth_and_no_trading(platform, db):
    seed(db, platform, trend(+1), lambda mins: 0.6, prev_closes=False)
    run_id = replay(platform, db)
    with db.session() as s:
        run = s.get(m.AlgoRun, run_id)
    assert run.status == "SKIPPED" and "previous closes" in run.skip_reason and positions(db) == []
