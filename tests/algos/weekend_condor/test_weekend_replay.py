"""Two-day replays: Friday's run sells and carries the condor, Monday's run takes it over and buys it back."""

from datetime import date, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tests.helpers import seed_day
from trading_core.db import models as m
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo

FRI, MON, THU = date(2026, 10, 30), date(2026, 11, 2), date(2026, 10, 29)
EXPIRY = date(2026, 11, 3)            # Tuesday: DTE 2 on Friday, 1 on Monday
CAL = MarketCalendar([])
# Synthetic ticks are 15 s apart, so the stale-feed limit is relaxed.
LOOSE_FEED = {"exits": {"stale_feed_seconds": 60}}


def replay(platform, db, day, overrides=LOOSE_FEED):
    return run_algo(platform, db, "weekend_condor", replay_date=day, overrides=overrides, calendar=CAL)


def positions(db):
    with db.session() as s:
        return s.scalars(select(m.Position).options(selectinload(m.Position.legs)).order_by(m.Position.position_id)).all()


def run_row(db, run_id):
    with db.session() as s:
        return s.get(m.AlgoRun, run_id), s.get(m.DailySummary, run_id)


def friday(db, platform, **kw):
    # decay 3.0 makes the far-OTM shorts rich enough for the 12-pt minimum credit in the toy pricing
    seed_day(db, platform, FRI, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 3.0,
             strikes=(23800, 26200), **kw)


def test_friday_sells_and_carries_then_monday_buys_back(platform, db):
    friday(db, platform)
    fri_run = replay(platform, db, FRI)
    [pos] = positions(db)
    run, summary = run_row(db, fri_run)
    assert pos.status == "OPEN" and pos.exit_time is None                 # held over the weekend
    assert time(14, 45) <= pos.entry_time.time() < time(15, 10)
    strikes = {l.role: l.strike for l in pos.legs}
    # VIX 14, ~2.8 days to Monday 09:15 -> expected move ~305 -> shorts ±1.2× -> 400 from ATM, hedges 200 out
    assert strikes == {"SHORT_PE": 24600, "SHORT_CE": 25400, "LONG_PE": 24400, "LONG_CE": 25600}
    assert pos.entry_credit_pts >= 12 and pos.params["exit_day"] == str(MON)
    assert run.status == "DONE" and summary.traded and summary.net_pnl is None
    assert "carried to the next trading day" in summary.notes

    # Monday: the weekend took value out of the options (decay 3.0 -> 2.2), spot unchanged.
    seed_day(db, platform, MON, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 2.2, strikes=(23800, 26200))
    mon_run = replay(platform, db, MON)
    [pos] = positions(db)
    run, summary = run_row(db, mon_run)
    assert pos.status == "CLOSED" and pos.run_id == fri_run              # still Friday's trade
    assert pos.exit_reason == "CARRY_EXIT" and time(9, 35) <= pos.exit_time.time() < time(9, 36)
    assert pos.gross_pnl > 0                                             # the weekend decay was kept
    assert run.status == "DONE" and summary.traded and summary.net_pnl == pytest.approx(pos.net_pnl)
    assert run.ended_at.time() < time(9, 40)                              # reported as soon as it was done
    with db.session() as s:
        exits = s.scalars(select(m.Order).where(m.Order.purpose == "EXIT")).all()
    assert len(exits) == 4 and {o.run_id for o in exits} == {mon_run}


def test_gap_against_the_position_is_stopped_at_monday_start(platform, db):
    friday(db, platform)
    replay(platform, db, FRI)
    # Monday opens 450 pts higher: beyond the short call. The stop acts as soon as Monday's run sees it.
    seed_day(db, platform, MON, EXPIRY, spot=lambda f: 25450.0, decay=lambda f: 2.5, strikes=(23800, 26200))
    replay(platform, db, MON)
    [pos] = positions(db)
    assert pos.status == "CLOSED" and pos.exit_reason in ("STOP_LOSS", "PREMIUM_BLOWOUT")
    assert pos.gross_pnl < -10 * 65 * 0.5
    assert pos.exit_time.time() < time(9, 16)                             # first ticks of the day


def test_thursday_is_not_an_entry_day(platform, db):
    seed_day(db, platform, THU, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 3.0)
    run_id = replay(platform, db, THU)
    run, _ = run_row(db, run_id)
    assert run.status == "SKIPPED" and "not the last day before a weekend" in run.skip_reason
    assert run.ended_at.time() < time(9, 0)                               # no waiting around until 15:30


def test_friday_before_a_monday_holiday_still_carries_to_tuesday(platform, db):
    cal = MarketCalendar([MON])
    friday(db, platform)
    run_algo(platform, db, "weekend_condor", replay_date=FRI, overrides=LOOSE_FEED, calendar=cal)
    [pos] = positions(db)
    assert pos.status == "OPEN" and pos.params["exit_day"] == str(EXPIRY)


def test_high_vix_skips_the_weekend(platform, db):
    friday(db, platform, vix=19.0)
    run_id = replay(platform, db, FRI)
    run, _ = run_row(db, run_id)
    assert run.status == "SKIPPED" and "VIX" in run.skip_reason
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(m.Position)) == 0


def test_dashboard_shows_the_carried_trade_on_both_days_and_counts_it_on_monday(platform, db):
    from dashboard import queries
    from dashboard import strategy_queries as sq

    friday(db, platform)
    replay(platform, db, FRI)
    fri_open = sq.strategy_day(db, "weekend_condor", FRI, "paper", "replay")
    assert fri_open["trades"][0]["carried_out"] and not fri_open["trades"][0]["carried_in"]

    seed_day(db, platform, MON, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 2.2, strikes=(23800, 26200))
    replay(platform, db, MON)
    [pos] = positions(db)

    fri = sq.strategy_day(db, "weekend_condor", FRI, "paper", "replay")
    mon = sq.strategy_day(db, "weekend_condor", MON, "paper", "replay")
    [ft], [mt] = fri["trades"], mon["trades"]
    assert ft["position_id"] == mt["position_id"] == pos.position_id
    assert ft["carried_out"] and ft["exit_date"] == str(MON)
    assert mt["carried_in"] and mt["net"] == pytest.approx(pos.net_pnl)
    assert mon["runs"][0]["trades"] == 1

    [fcard] = [c for c in queries.overview(db, FRI, "paper", "replay")["strategies"] if c["algo_id"] == "weekend_condor"]
    [mcard] = [c for c in queries.overview(db, MON, "paper", "replay")["strategies"] if c["algo_id"] == "weekend_condor"]
    assert fcard["net"] is None and fcard["carried"][0]["closed_on"] == str(MON)
    assert mcard["net"] == pytest.approx(pos.net_pnl) and mcard["carried_in"] == 1
    assert mcard["charges"] == pytest.approx(pos.charges)                   # Friday's entry charges included

    j = sq.journal(db, "weekend_condor", FRI, MON, "paper", "replay")
    by_day = {d["date"]: d for d in j["days"]}
    assert by_day[str(FRI)]["trades"][0]["carried_out"] and by_day[str(MON)]["trades"][0]["carried_in"]
    perf = sq.performance(db, "weekend_condor", FRI, MON, "paper", "replay")
    assert perf["daily"] == [[str(MON), pytest.approx(pos.net_pnl)]]       # counted on the exit day
