"""Full-day replays of synthetic recorded data through the real runner, the balanced condor, the paper
broker and the DB."""

from datetime import date, time

import pytest

from tests.algos.iron_condor.test_replay_e2e import count, one
from tests.helpers import seed_day
from trading_core.db import models as m
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo

TRADE_DATE = date(2026, 10, 23)    # Friday
EXPIRY = date(2026, 10, 27)        # Tuesday -> DTE 2 -> width 600, hedge 300, stop/target 15
CAL = MarketCalendar([])
# Ticks in these synthetic days are 15 s apart, so the stale-feed limit is relaxed.
DEFAULTS = {"exits": {"stale_feed_seconds": 60}}


def run(platform, db, overrides=DEFAULTS, algo="iron_condor_balanced", **seed):
    seed_day(db, platform, TRADE_DATE, EXPIRY, **seed)
    return run_algo(platform, db, algo, replay_date=TRADE_DATE, overrides=overrides, calendar=CAL)


def strikes(pos):
    return {l.role: l.strike for l in pos.legs}


def test_unbalanced_day_trades_with_balanced_strikes(platform, db):
    """Calls twice as dear: the original condor waits and skips; this one sells the call further out."""
    run_id = run(platform, db, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f, ce_skew=2.0)
    pos = one(db, m.Position, run_id=run_id)
    assert time(9, 45) <= pos.entry_time.time() < time(10, 0)
    assert strikes(pos) == {"LONG_PE": 24450, "SHORT_PE": 24750, "SHORT_CE": 25350, "LONG_CE": 25650}
    assert pos.params["strike_mode"] == "balanced" and pos.params["premium_richer"] == "CE"
    assert pos.params["stop_points"] == 15 and pos.params["target_points"] == 15

    balance = one(db, m.RunCheck, run_id=run_id, check_name="premium_balance")
    assert balance.passed and balance.value == pytest.approx(2.0, rel=0.05)
    assert balance.detail["log_only"]
    picked = one(db, m.RunCheck, run_id=run_id, check_name="strikes")
    assert picked.detail["width_ce"] == 25300 and picked.detail["short_ce"] == 25350


def test_same_day_skipped_by_the_original(platform, db):
    run_id = run(platform, db, overrides={"exits": {"stale_feed_seconds": 60}}, algo="iron_condor",
                 spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f, ce_skew=2.0)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "imbalance" in r.skip_reason


def test_width_mode_sells_the_original_strikes_without_waiting(platform, db):
    run_id = run(platform, db, overrides={**DEFAULTS, "strikes": {"mode": "width"}},
                 spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f, ce_skew=2.0)
    pos = one(db, m.Position, run_id=run_id)
    assert strikes(pos) == {"LONG_PE": 24400, "SHORT_PE": 24700, "SHORT_CE": 25300, "LONG_CE": 25600}
    assert count(db, m.RunCheck, run_id=run_id, check_name="strikes") == 1


def test_flat_day_runs_to_the_time_exit_with_the_dte_stop(platform, db):
    run_id = run(platform, db, spot=lambda f: 25000.0, decay=lambda f: 1.0 - 0.3 * f)
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason == "TIME_EXIT" and pos.exit_time.time() >= time(15, 15)
    assert pos.net_pnl == pytest.approx(pos.gross_pnl - pos.charges)
    assert one(db, m.DailySummary, run_id=run_id).traded


def test_trend_hits_the_dte_stop(platform, db):
    run_id = run(platform, db, spot=lambda f: 25000.0 + 1600 * f, decay=lambda f: 1.0, strikes=(24300, 26000))
    pos = one(db, m.Position, run_id=run_id)
    assert pos.exit_reason in ("STOP_LOSS", "PREMIUM_BLOWOUT") and pos.gross_pnl < 0


def test_min_credit_by_dte_waits_until_10_30_then_skips(platform, db):
    overrides = {**DEFAULTS, "entry": {"min_credit_by_dte": {0: 5, 2: 500}}}
    run_id = run(platform, db, overrides=overrides, spot=lambda f: 25000.0, decay=lambda f: 1.0)
    r = one(db, m.AlgoRun, run_id=run_id)
    assert r.status == "SKIPPED" and "no entry by 10:30" in r.skip_reason and "net credit" in r.skip_reason
    assert count(db, m.Position) == 0
