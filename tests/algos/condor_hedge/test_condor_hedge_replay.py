"""Condor move hedge replays on synthetic days (NIFTY, VIX and the option chain every 15 s)."""

from __future__ import annotations

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.helpers import seed_day
from trading_core.clock import ist
from trading_core.db import models as m
from trading_core.db.repo import Journal
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo
from trading_core.strategy.position import Leg, Position

DAY, EXPIRY = date(2026, 10, 23), date(2026, 10, 27)       # Friday, DTE 2
CAL = MarketCalendar([])
# Synthetic ticks are 15 s apart: relax the feed and quote-age limits.
LOOSE = {"exits": {"stale_feed_seconds": 60}, "option": {"max_quote_age_s": 20}}
BY_TIME = {**LOOSE, "anchor": {"source": "time"}}


def at(minutes: float) -> float:
    """seed_day's session fraction for ``minutes`` after 09:15."""
    return minutes / 375


def path(points: list[tuple[float, float]]):
    """Piecewise-linear NIFTY through (minutes after 09:15, level) points; flat outside them."""
    def spot(frac: float) -> float:
        mins = frac * 375
        if mins <= points[0][0]:
            return points[0][1]
        for (m0, s0), (m1, s1) in zip(points, points[1:]):
            if mins <= m1:
                return s0 + (s1 - s0) * (mins - m0) / (m1 - m0)
        return points[-1][1]
    return spot


def replay(platform, db, overrides):
    return run_algo(platform, db, "condor_hedge", replay_date=DAY, overrides=overrides, calendar=CAL)


def positions(db):
    with db.session() as s:
        return s.scalars(select(m.Position).where(m.Position.algo_id == "condor_hedge")
                         .options(selectinload(m.Position.legs)).order_by(m.Position.position_id)).all()


def run_of(db, run_id):
    with db.session() as s:
        return s.get(m.AlgoRun, run_id)


def test_fall_away_from_the_anchor_buys_an_otm_put_and_profits(platform, db):
    # Flat at 25000 to 10:30, then -3 pts a minute to 24550 by 13:00, then flat.
    seed_day(db, platform, DAY, EXPIRY, spot=path([(75, 25000), (225, 24550)]), decay=lambda f: 1.0)
    replay(platform, db, BY_TIME)
    [p] = positions(db)
    leg = p.legs[0]
    assert p.params["side"] == "PE" and p.params["anchor"] == 25000 and p.params["move"] <= -80
    assert leg.tradingsymbol.endswith("PE") and leg.qty == 65
    assert leg.strike == round(p.params["spot"] / 50) * 50 - 100                 # two strikes OTM
    assert p.entry_time.time() >= time(10, 30) and p.status == "CLOSED" and p.net_pnl > 0


def test_flat_day_never_triggers(platform, db):
    seed_day(db, platform, DAY, EXPIRY, spot=path([(0, 25000), (375, 25040)]), decay=lambda f: 1.0)
    run_id = replay(platform, db, BY_TIME)
    assert positions(db) == []
    r = run_of(db, run_id)
    assert r.status == "SKIPPED" and "needs ±80" in r.skip_reason


def test_bounce_after_entry_exits_on_the_nifty_trail(platform, db):
    # Down 200 pts by 12:15, then back up 150: the trade exits once NIFTY gives back 80 from its low.
    seed_day(db, platform, DAY, EXPIRY, spot=path([(75, 25000), (180, 24800), (250, 24950)]), decay=lambda f: 1.0)
    replay(platform, db, BY_TIME)
    [p] = positions(db)
    assert p.exit_reason == "TRAILING_STOP" and p.spot_at_exit >= 24800 + 80 - 5


def test_live_anchor_is_the_condors_entry(platform, db):
    # The condor sold at NIFTY 25100 at 09:50; NIFTY sits at 25000: already 100 pts down, so the hedge
    # buys a put as soon as entries open at 10:00.
    seed_day(db, platform, DAY, EXPIRY, spot=path([(0, 25000), (375, 25000)]), decay=lambda f: 1.0)
    j = Journal(db)
    now = ist(DAY, time(9, 50))
    j.register_algo("iron_condor_balanced", "condor", "0", "", now)
    run_id = j.create_run("iron_condor_balanced", DAY, "paper", "replay", {}, now)
    pos = Position("IRON_CONDOR", "NIFTY", [Leg("SHORT_PE", 1, "X", "SELL", 65, entry_price=50.0)], status="OPEN")
    pos.entry_time, pos.spot_at_entry = now, 25100.0
    j.create_position(run_id, "iron_condor_balanced", "paper", pos)
    j.update_position(pos)

    replay(platform, db, LOOSE)
    [p] = positions(db)
    assert p.params["anchor"] == 25100 and "iron_condor_balanced entry 09:50" in p.params["anchor_from"]
    assert p.params["side"] == "PE" and time(10, 0) <= p.entry_time.time() <= time(10, 2)


def test_no_condor_trade_means_nothing_to_hedge(platform, db):
    seed_day(db, platform, DAY, EXPIRY, spot=path([(75, 25000), (225, 24550)]), decay=lambda f: 1.0)
    run_id = replay(platform, db, LOOSE)
    assert positions(db) == []
    r = run_of(db, run_id)
    assert r.status == "SKIPPED" and "nothing to hedge" in r.skip_reason
