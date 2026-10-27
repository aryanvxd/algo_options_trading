"""Synthetic expiry days recorded every second, replayed through the real runner, algo, paper broker and DB."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.helpers import NIFTY_TOKEN, VIX_TOKEN, instrument_rows, option_token, tick
from trading_core.clock import ist
from trading_core.db import models as m
from trading_core.db.repo import Journal
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.calendar import MarketCalendar
from trading_core.marketdata import greeks
from trading_core.strategy.bootstrap import run_algo

DAY = date(2026, 10, 27)          # Tuesday, expiry day -> DTE 0
FUT_TOKEN = 13_000_001
CAL = MarketCalendar([])
STRIKES = range(24600, 25450, 50)


def seed(db, platform, spot_at, iv=0.16, start=time(9, 40), end=time(10, 50)):
    """What a 1-second recording run would store. ``spot_at(seconds since 09:45)`` gives NIFTY."""
    rows = instrument_rows(DAY, 24000, 26000) + [dict(
        instrument_token=FUT_TOKEN, exchange_token=None, tradingsymbol="NIFTY26NOVFUT", name="NIFTY",
        exchange="NFO", segment="NFO-FUT", instrument_type="FUT", strike=0, expiry=date(2026, 11, 24),
        lot_size=65, tick_size=0.05)]
    InstrumentStore(db, platform.instruments).save_snapshot(DAY, rows)
    journal = Journal(db)
    journal.register_algo("recorder", "recorder", "0", "", ist(DAY, time(8, 0)))
    run_id = journal.create_run("recorder", DAY, "paper", "live", {}, ist(DAY, time(8, 0)))
    t, stop, base = ist(DAY, start), ist(DAY, end), ist(DAY, time(9, 45))
    batch = []
    while t <= stop:
        s = spot_at((t - base).total_seconds())
        tte = greeks.years_between(t, DAY)
        batch += [tick(NIFTY_TOKEN, ltp=round(s, 2), when=t), tick(VIX_TOKEN, ltp=13.0, when=t),
                  tick(FUT_TOKEN, ltp=round(s + 40, 2), when=t)]
        for k in STRIKES:
            for opt in ("CE", "PE"):
                p = float(greeks.price(s, k, tte, 0.065, iv, opt == "CE"))
                p = max(0.05, round(p * 20) / 20)
                batch.append(tick(option_token(k, opt), ltp=p, bid=max(0.05, round(p - 0.05, 2)),
                                  ask=round(p + 0.05, 2), when=t))
        if len(batch) > 20000:
            journal.add_ticks(run_id, batch)
            batch = []
        t += timedelta(seconds=1)
    journal.add_ticks(run_id, batch)


def run(platform, db, overrides=None):
    return run_algo(platform, db, "strangle_scalp", replay_date=DAY, overrides=overrides, calendar=CAL)


def positions(db, run_id):
    with db.session() as s:
        return s.scalars(select(m.Position).options(selectinload(m.Position.legs))
                         .where(m.Position.run_id == run_id).order_by(m.Position.position_id)).all()


def burst(at_s, size, over_s):
    """Flat 25,000, then a move of ``size`` points spread over ``over_s`` seconds, starting at ``at_s``."""
    def spot(sec):
        if sec < at_s:
            return 25000.0
        return 25000.0 + size * min(1.0, (sec - at_s) / over_s)
    return spot


def test_burst_is_bought_and_sold_at_target_then_the_day_waits_for_more(platform, db):
    seed(db, platform, burst(15 * 60, 80, 60))            # +80 pts in a minute from 10:00
    run_id = run(platform, db)

    with db.session() as s:
        r = s.get(m.AlgoRun, run_id)
        assert r.status == "DONE"
        checks = {}                                   # first check of each name
        for c in s.scalars(select(m.RunCheck).where(m.RunCheck.run_id == run_id).order_by(m.RunCheck.id)):
            checks.setdefault(c.check_name, c)
        events = [e.type for e in s.scalars(select(m.Event).where(m.Event.run_id == run_id))]
        summary = s.get(m.DailySummary, run_id)

    [pos] = positions(db, run_id)
    assert pos.structure == "LONG_STRANGLE" and pos.status == "CLOSED"
    assert pos.exit_reason == "TARGET"
    assert time(10, 0, 10) <= pos.entry_time.time() < time(10, 1)    # ~15 s into the burst
    assert {l.role for l in pos.legs} == {"LONG_CE", "LONG_PE"}
    assert all(l.side == "BUY" and l.qty == 65 for l in pos.legs)
    picked = checks["pair"].detail["pair"]                       # the band applies when the pair is picked
    assert 15 <= picked["ce_mid"] <= 30 and 15 <= picked["pe_mid"] <= 30
    assert pos.gross_pnl >= 7.7 * 65 - 2 * 0.1 * 65            # target, less the exit fills' slippage
    premium = sum(l.entry_price * l.qty for l in pos.legs)
    assert pos.capital_source == "premium"
    assert premium < pos.capital_required < premium + 100       # premium paid + entry charges
    assert pos.spot_at_entry is not None and pos.spot_at_exit > pos.spot_at_entry
    assert pos.params["signal"]["driver"] == "CE" and pos.params["dte"] == 0
    assert checks["signal"].passed and checks["pair"].passed
    assert "REENTRY" in events and "ENTRIES_STOPPED" in events
    assert summary.traded and summary.net_pnl == round(pos.gross_pnl - pos.charges, 2)
    assert summary.exit_reason == "TARGET"


def test_flat_day_never_trades_and_is_skipped(platform, db):
    seed(db, platform, lambda sec: 25000.0)
    run_id = run(platform, db)
    with db.session() as s:
        r = s.get(m.AlgoRun, run_id)
    assert r.status == "SKIPPED" and "10:30" in r.skip_reason
    assert positions(db, run_id) == []


def test_two_losing_trades_in_a_row_stop_the_day(platform, db):
    # Short bursts that fade straight back: each is bought near the top, then the move dies and the
    # stall exit sells it at a loss. The third burst must not be traded.
    def fades(sec):
        for start in (15 * 60, 20 * 60, 25 * 60):
            if start <= sec < start + 20:
                return 25000.0 + 35 * (sec - start) / 20
            if start + 20 <= sec < start + 30:
                return 25035.0 - 35 * (sec - start - 20) / 10
        return 25000.0
    seed(db, platform, fades)
    run_id = run(platform, db)
    trades = positions(db, run_id)
    assert len(trades) == 2
    assert all(p.net_pnl < 0 and p.exit_reason == "STALL" for p in trades)
    with db.session() as s:
        summary = s.get(m.DailySummary, run_id)
    assert "2 losing trades in a row" in summary.notes and "2 trades" in summary.notes


def test_restart_after_a_closed_trade_keeps_looking_for_entries(platform, db, monkeypatch):
    """The window was closed at 10:05, after trade 1: rerunning resumes the day with that trade counted."""
    from types import SimpleNamespace

    from sqlalchemy import delete

    import trading_core.feed.live_feed as live_feed
    import trading_core.kite.client as kite_client
    import trading_core.strategy.bootstrap as bootstrap
    from tests.helpers import FakeFeed
    from trading_core.clock import SimClock
    from trading_core.config import KiteSecrets

    seed(db, platform, burst(15 * 60, 80, 60))
    run_id = run(platform, db)
    with db.session() as s:     # make it look like a live run closed before its report
        r = s.get(m.AlgoRun, run_id)
        r.source, r.status, r.ended_at = "live", "IN_TRADE", None
        s.execute(delete(m.DailySummary))
        s.execute(delete(m.Event).where(m.Event.run_id == run_id))

    monkeypatch.setattr(bootstrap, "load_secrets", lambda root: KiteSecrets(api_key="k", api_secret="s"))
    monkeypatch.setattr(kite_client.KiteClient, "from_db", classmethod(lambda cls, *a: SimpleNamespace(access_token="t")))
    monkeypatch.setattr(live_feed, "LiveFeed", lambda *a: FakeFeed(age=None))
    assert run_algo(platform, db, "strangle_scalp", clock=SimClock(ist(DAY, time(10, 5))), calendar=CAL) == run_id

    with db.session() as s:
        r = s.get(m.AlgoRun, run_id)
        events = {e.type: e.message for e in s.scalars(select(m.Event).where(m.Event.run_id == run_id))}
        summary = s.get(m.DailySummary, run_id)
    [pos] = positions(db, run_id)
    assert r.status == "DONE"                                    # not SKIPPED: it traded today
    assert "still looking for entries" in events["RESUME"]
    assert "10:30" in events["ENTRIES_STOPPED"]
    assert summary.traded and summary.net_pnl == pos.net_pnl
