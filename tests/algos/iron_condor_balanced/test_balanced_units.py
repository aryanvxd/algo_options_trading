from datetime import time
from types import SimpleNamespace

import pytest

from algos.iron_condor_balanced.exits import ProfitTrail
from algos.iron_condor_balanced.settings import IronCondorBalancedConfig
from algos.iron_condor_balanced.strike_selector import pick_balanced
from tests.algos.iron_condor.test_iron_condor_units import chain, quote_fn
from trading_core.risk.engine import RiskContext
from trading_core.strategy import registry


def legs(pick):
    p = pick.plan
    return p.long_pe, p.short_pe, p.short_ce, p.long_ce


def test_equal_premiums_keep_the_same_width_strikes():
    pick = pick_balanced(25000, 600, 300, 50, chain(), quote_fn(), min_distance=250, max_shift=150)
    assert legs(pick) == (24400, 24700, 25300, 25600)
    assert pick.pe_mid == pytest.approx(pick.ce_mid)


def test_dear_calls_move_the_call_out_and_the_put_in():
    # Calls twice as dear: same-width PE 24700 ≈ 8.1, CE 25300 ≈ 16.25, target ≈ 12.2.
    pick = pick_balanced(25000, 600, 300, 50, chain(), quote_fn(ce_skew=2.0), min_distance=250, max_shift=150)
    assert (pick.width_pe, pick.width_ce) == (24700, 25300)
    assert pick.target == pytest.approx(12.175, abs=0.05)
    assert legs(pick) == (24450, 24750, 25350, 25650)
    assert abs(pick.pe_mid - pick.ce_mid) < 1.0


def test_min_distance_keeps_a_short_away_from_spot():
    pick = pick_balanced(25000, 600, 300, 50, chain(), quote_fn(ce_skew=2.0), min_distance=300, max_shift=150)
    assert (pick.plan.short_pe, pick.plan.short_ce) == (24700, 25350)


def test_zero_max_shift_is_the_original_construction():
    pick = pick_balanced(25000, 600, 300, 50, chain(), quote_fn(ce_skew=2.0), min_distance=250, max_shift=0)
    assert legs(pick) == (24400, 24700, 25300, 25600)


def test_waits_for_prices_of_the_same_width_shorts():
    from tests.helpers import option_token

    missing = {option_token(24700, "PE")}
    assert pick_balanced(25000, 600, 300, 50, chain(), quote_fn(missing=missing), 250, 150) is None


def test_tables_by_dte():
    cfg = IronCondorBalancedConfig(algo_id="iron_condor_balanced", name="x")
    assert [cfg.stop_for(d) for d in range(5)] == [5, 8, 15, 15, 15]
    assert [cfg.target_for(d) for d in range(5)] == [5, 8, 15, 15, 15]
    assert [cfg.min_credit_for(d) for d in range(5)] == [5, 10, 10, 10, 10]
    assert [cfg.min_distance_for(d) for d in range(5)] == [225, 225, 250, 250, 250]


def test_config_yaml_matches_the_spec():
    _, cfg = registry.load("iron_condor_balanced")
    assert cfg.strikes.mode == "balanced" and cfg.strikes.max_shift_pts == 150
    assert cfg.schedule.entry == time(9, 45) and cfg.schedule.entry_deadline == time(10, 30)
    assert cfg.exits.stop_loss_points is None and cfg.exits.target_points is None
    assert [cfg.stop_for(d) for d in range(5)] == [5, 8, 15, 15, 15]
    assert [cfg.min_credit_for(d) for d in range(5)] == [5, 10, 10, 10, 10]
    assert not hasattr(cfg.filters, "max_premium_ratio")     # the balance is logged only
    assert cfg.lots == 2 and cfg.trade_window_days == 5


def trail_ctx(pnl, complete=True):
    mark = SimpleNamespace(gross_pnl=pnl, net_pnl=pnl - 200, complete=complete)
    return RiskContext(now=None, position=None, mark=mark, feed_age_s=0.5)


def test_trail_waits_until_armed_and_never_acts_before_it():
    rule = ProfitTrail(780, 390, "gross")
    assert [rule.check(trail_ctx(p)) for p in (100, 700, 300, -900, 779)] == [None] * 5


def test_trail_exits_the_give_back_below_the_peak():
    rule = ProfitTrail(780, 390, "gross")
    for p in (500, 780, 1000, 1254, 900):
        assert rule.check(trail_ctx(p)) is None           # 1254 - 390 = 864
    assert "peak 1254" in rule.check(trail_ctx(864))


def test_trail_ignores_an_incomplete_mark_and_can_use_net_pnl():
    rule = ProfitTrail(780, 390, "gross")
    assert rule.check(trail_ctx(1000)) is None
    assert rule.check(trail_ctx(-500, complete=False)) is None
    net = ProfitTrail(780, 390, "net")
    assert net.check(trail_ctx(1000)) is None             # net 800: armed
    assert net.check(trail_ctx(600)) is not None          # net 400 <= 800 - 390


def test_trail_table_is_off_on_0_and_1_dte():
    _, cfg = registry.load("iron_condor_balanced")
    assert [cfg.trail_for(d) for d in range(5)] == [(0, 0), (0, 0), (6, 3), (6, 3), (6, 3)]
    assert cfg.version == "0.2.0"


def test_original_condor_is_unchanged():
    _, cfg = registry.load("iron_condor")
    assert cfg.version == "0.3.0" and cfg.filters.max_premium_ratio == 1.5
    assert cfg.schedule.entry_deadline == time(10, 0) and cfg.exits.stop_loss_points == 5
