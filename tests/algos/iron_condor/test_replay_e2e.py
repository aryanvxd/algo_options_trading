"""Full-day replays of synthetic recorded data through the real runner, algo, paper broker and DB."""

from datetime import date, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tests.helpers import seed_day
from trading_core.db import models as m
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo

TRADE_DATE = date(2026, 10, 23)    # Friday
EXPIRY = date(2026, 10, 27)        # Tuesday -> DTE 2 -> width 600, hedge 300
CAL = MarketCalendar([])

# Wide stop/target (77 pts ≈ ₹5,000 on 1 lot) so the scenario's own exit rule is the one that fires.
LOOSE = {"exits": {"pnl_basis": "gross", "stop_loss_points": 77, "target_points": 77, "stale_feed_seconds": 60}}
# Ticks in these synthetic days are 15 s apart, so the stale-feed limit is relaxed.
DEFAULTS = {"exits": {"stale_feed_seconds": 60}}


def run(platform, db, overrides, **seed):
    seed_day(db, platform, TRADE_DATE, EXPIRY, **seed)
    return run_algo(platform, db, "iron_condor", replay_date=TRADE_DATE, overrides=overrides, calendar=CAL)


def count(db, model, **where):
    with db.session() as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return s.scalar(q)


def one(db, model, **where):
    with db.session() as s:
        q = select(model)
        if model is m.Position:
            q = q.options(selectinload(m.Position.legs))
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return s.scalars(q).one()


def test_flat_day_time_exit_writes_a_consistent_journal(platform, db):
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)

    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "DONE" and r.source == "replay"
    pos = one(db, m.Position, run_id=run_id)
    assert pos.status == "CLOSED" and pos.exit_reason == "TIME_EXIT"
    assert time(9, 45) <= pos.entry_time.time() < time(10, 0)
    assert pos.exit_time.time() >= time(15, 15)
    legs = {l.role: l for l in pos.legs}
    assert {k: v.strike for k, v in legs.items()} == {"LONG_PE": 24400, "SHORT_PE": 24700,
                                                      "SHORT_CE": 25300, "LONG_CE": 25600}
    assert count(db, m.Order, run_id=run_id) == 8
    assert count(db, m.Order, run_id=run_id, status="COMPLETE") == 8
    assert count(db, m.Fill) == 8 and count(db, m.OrderCharge) == 8

    with db.session() as s:
        total_charges = s.scalar(select(func.sum(m.OrderCharge.total)))
    assert pos.charges == pytest.approx(total_charges)
    assert pos.net_pnl == pytest.approx(pos.gross_pnl - pos.charges)
    assert pos.gross_pnl == pytest.approx(sum(l.pnl for l in pos.legs))

    summary = one(db, m.DailySummary, run_id=run_id)
    assert summary.traded and summary.net_pnl == pytest.approx(pos.net_pnl)
    assert summary.exit_reason == "TIME_EXIT"
    assert count(db, m.PositionSnapshot) > 100
    assert count(db, m.Event, run_id=run_id, type="ENTRY") == 1
    assert (platform.reports_dir / "iron_condor" / f"{TRADE_DATE}_replay_{run_id}" / "summary.txt").exists()


def test_rally_triggers_premium_blowout(platform, db):
    # Entry is at 09:45, after the rally has started, so the call hedge sits above the default strikes.
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0 + 1600 * f, decay=lambda f: 1.0,
                 strikes=(24300, 26000))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "PREMIUM_BLOWOUT" and pos.gross_pnl < 0


def test_decay_hits_target(platform, db):
    overrides = {"exits": {**LOOSE["exits"], "target_points": 4.6}}     # ≈ ₹300 on 1 lot
    run_id = run(platform, db, overrides, spot=lambda f: 25000.0, decay=lambda f: max(0.05, 1.0 - 2.0 * f))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TARGET" and pos.gross_pnl > 0   # trigger was on the book; fills pay slippage
    assert pos.exit_time.time() < time(15, 0)


def test_high_vix_skips_day_without_trading(platform, db):
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0, decay=lambda f: 1.0, vix=22.0)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "VIX" in r.skip_reason
    assert count(db, m.Position) == 0
    assert not one(db, m.DailySummary, run_id=run_id).traded


def test_non_window_day_skipped(platform, db):
    seed_day(db, platform, TRADE_DATE, date(2026, 11, 3), spot=lambda f: 25000.0, decay=lambda f: 1.0)
    run_id = run_algo(platform, db, "iron_condor", replay_date=TRADE_DATE, overrides=LOOSE, calendar=CAL)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "trading days to expiry" in r.skip_reason


def test_pdf_net_stop_fires_almost_immediately_for_one_lot(platform, db):
    """The PDF defaults (net P&L after charges, about -₹300 stop) on 1 lot with pessimistic fills
    (0.5 pt slippage per leg): spread + slippage + ~₹190 of charges put the trade below -₹300 the
    moment it opens. This documents that finding."""
    pdf = {"lots": 1, "exits": {"pnl_basis": "net", "stop_loss_points": 4.6, "target_points": 7.7,
                                "stale_feed_seconds": 60}}
    platform.paper.slippage_pts = 0.5
    run_id = run(platform, db, pdf, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "STOP_LOSS"
    assert (pos.exit_time - pos.entry_time).total_seconds() <= 60


def test_default_points_stop_survives_entry_costs_on_a_quiet_day(platform, db):
    """Defaults: gross basis, stop 5 pts / target 8 pts (₹650 / ₹1,040 on 2 lots). A quiet day must not
    be stopped out by entry spread + slippage; net P&L is still recorded."""
    run_id = run(platform, db, DEFAULTS, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TIME_EXIT"
    assert pos.net_pnl == pytest.approx(pos.gross_pnl - pos.charges)
    with db.session() as s:
        nets = s.scalars(select(m.PositionSnapshot.net_pnl)).all()
    assert nets and all(n is not None for n in nets)


def test_default_points_stop_fires_on_a_trend(platform, db):
    run_id = run(platform, db, DEFAULTS, spot=lambda f: 25000.0 + 1600 * f, decay=lambda f: 1.0,
                 strikes=(24300, 26000))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason in ("STOP_LOSS", "PREMIUM_BLOWOUT")


def test_unbalanced_premiums_wait_until_the_deadline_then_skip(platform, db):
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0, decay=lambda f: 1.0, ce_skew=2.0)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "no entry by 10:00" in r.skip_reason and "CE richer" in r.skip_reason
    assert count(db, m.Position) == 0
    check = one(db, m.RunCheck, run_id=run_id, check_name="premium_balance")
    assert not check.passed and check.value == pytest.approx(2.0, rel=0.05)


def test_entry_journals_premium_balance_and_pcr(platform, db):
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    balance = one(db, m.RunCheck, run_id=run_id, check_name="premium_balance")
    assert balance.passed and balance.value == pytest.approx(1.0, abs=0.05)
    pcr = one(db, m.RunCheck, run_id=run_id, check_name="pcr")
    assert pcr.passed and pcr.value == pytest.approx(1.2)          # seeded OI: CE 1000, PE 1200
    assert count(db, m.RunCheck, run_id=run_id, check_name="gap_pct") == 0
    assert count(db, m.RunCheck, run_id=run_id, check_name="adv_dec") == 0
    pos = one(db, m.Position, run_id=run_id)
    assert pos.params["pcr"] == pytest.approx(1.2) and pos.params["premium_richer"] in ("CE", "PE", "equal")


def test_late_start_after_entry_time_still_trades(platform, db):
    """The laptop comes on at ~09:35, or later: steps already due run at once, entry until 10:00."""
    from trading_core.clock import SimClock, ist

    seed_day(db, platform, TRADE_DATE, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    run_id = run_algo(platform, db, "iron_condor", replay_date=TRADE_DATE, overrides=LOOSE, calendar=CAL,
                      clock=SimClock(ist(TRADE_DATE, time(9, 50))))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TIME_EXIT" and time(9, 50) <= pos.entry_time.time() < time(10, 0)
