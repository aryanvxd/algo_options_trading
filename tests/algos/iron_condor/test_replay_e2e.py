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

# Wide rupee stop/target so the scenario's own exit rule is the one that fires.
LOOSE = {"exits": {"pnl_basis": "gross", "stop_loss_rupees": 5000, "target_rupees": 5000,
                   "stop_loss_points": None, "target_points": None, "stale_feed_seconds": 60}}
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
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0 + 1600 * f, decay=lambda f: 1.0)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "PREMIUM_BLOWOUT" and pos.gross_pnl < 0


def test_decay_hits_target(platform, db):
    overrides = {"exits": {**LOOSE["exits"], "target_rupees": 300}}
    run_id = run(platform, db, overrides, spot=lambda f: 25000.0, decay=lambda f: max(0.05, 1.0 - 2.0 * f))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TARGET" and pos.gross_pnl > 0   # trigger was on the book; fills pay slippage
    assert pos.exit_time.time() < time(15, 0)


def test_high_vix_skips_day_without_trading(platform, db):
    run_id = run(platform, db, LOOSE, spot=lambda f: 25000.0, decay=lambda f: 1.0, vix=22.0)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "vix" in r.skip_reason
    assert count(db, m.Position) == 0
    assert not one(db, m.DailySummary, run_id=run_id).traded


def test_non_window_day_skipped(platform, db):
    seed_day(db, platform, TRADE_DATE, date(2026, 11, 3), spot=lambda f: 25000.0, decay=lambda f: 1.0)
    run_id = run_algo(platform, db, "iron_condor", replay_date=TRADE_DATE, overrides=LOOSE, calendar=CAL)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "trading days to expiry" in r.skip_reason


def test_pdf_rupee_stop_fires_almost_immediately_for_one_lot(platform, db):
    """The PDF defaults (net P&L after charges, -₹300 stop) on 1 lot: spread + slippage + ~₹190 of
    charges put the trade below -₹300 the moment it opens. This documents that finding."""
    pdf = {"exits": {"pnl_basis": "net", "stop_loss_rupees": 300, "target_rupees": 500,
                     "stop_loss_points": None, "target_points": None, "stale_feed_seconds": 60}}
    run_id = run(platform, db, pdf, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "STOP_LOSS"
    assert (pos.exit_time - pos.entry_time).total_seconds() <= 60


def test_default_points_stop_survives_entry_costs_on_a_quiet_day(platform, db):
    """Defaults: gross basis, stop 5 pts / target 8 pts (₹325 / ₹520 on 1 lot). A quiet day must not
    be stopped out by entry spread + slippage; net P&L is still recorded."""
    run_id = run(platform, db, DEFAULTS, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TIME_EXIT"
    assert pos.net_pnl == pytest.approx(pos.gross_pnl - pos.charges)
    with db.session() as s:
        nets = s.scalars(select(m.PositionSnapshot.net_pnl)).all()
    assert nets and all(n is not None for n in nets)


def test_default_points_stop_fires_on_a_trend(platform, db):
    run_id = run(platform, db, DEFAULTS, spot=lambda f: 25000.0 + 1600 * f, decay=lambda f: 1.0)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason in ("STOP_LOSS", "PREMIUM_BLOWOUT")
