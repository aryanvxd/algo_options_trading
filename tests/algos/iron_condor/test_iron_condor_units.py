from datetime import date, datetime

from algos.iron_condor import filters
from algos.iron_condor.exits import PremiumBlowout
from algos.iron_condor.settings import FilterConfig, IronCondorConfig
from algos.iron_condor.strike_selector import Rejection, Selection, plan_strikes, select_condor
from tests.helpers import LOT_SIZE, option_price, option_token, tick
from trading_core.broker.base import BUY, SELL
from trading_core.broker.charges import ChargeCalculator
from trading_core.clock import IST
from trading_core.config import ChargeRates
from trading_core.kite.instruments import InstrumentInfo
from trading_core.market.premarket import PremarketSnapshot
from trading_core.risk.engine import RiskContext
from trading_core.strategy.position import Leg, Position

EXPIRY = date(2026, 10, 27)
NOW = datetime(2026, 10, 23, 9, 20, tzinfo=IST)


def chain(lo=23500, hi=26500, step=50):
    return [InstrumentInfo(option_token(k, o), f"NIFTY{k}{o}", "NIFTY", "NFO", "NFO-OPT", o, float(k), EXPIRY,
                           LOT_SIZE, 0.05) for k in range(lo, hi + step, step) for o in ("CE", "PE")]


def quote_fn(spot=25000.0, decay=1.0, spread=0.2, missing=()):
    def q(token):
        if token in missing:
            return None
        k, o = (token - 10_000_000) // 10, "CE" if token % 10 == 1 else "PE"
        p = option_price(o, k, spot, decay)
        return tick(token, p, max(0.05, round(p - spread / 2, 2)), round(p + spread / 2, 2))
    return q


def test_width_and_hedge_by_dte():
    cfg = IronCondorConfig(algo_id="iron_condor", name="x")
    assert [cfg.width_for(d) for d in (0, 1, 2, 4)] == [500, 550, 600, 600]
    assert [cfg.hedge_for(d) for d in (0, 1, 2)] == [200, 250, 300]


def test_plan_for_each_dte():
    k = [float(x) for x in range(23500, 26550, 50)]
    p2 = plan_strikes(25012, 600, 300, 50, k, k)
    assert (p2.long_pe, p2.short_pe, p2.short_ce, p2.long_ce) == (24400, 24700, 25300, 25600)
    p1 = plan_strikes(25012, 550, 250, 50, k, k)    # 275 from ATM: ties go further OTM
    assert (p1.long_pe, p1.short_pe, p1.short_ce, p1.long_ce) == (24450, 24700, 25300, 25550)
    p0 = plan_strikes(24990, 500, 200, 50, k, k)
    assert (p0.long_pe, p0.short_pe, p0.short_ce, p0.long_ce) == (24550, 24750, 25250, 25450)


def test_plan_rounds_to_listed_strikes_only():
    sparse = [24000.0, 24500.0, 25000.0, 25500.0, 26000.0]
    p = plan_strikes(25000, 600, 300, 50, sparse, sparse)
    assert (p.long_pe, p.short_pe, p.short_ce, p.long_ce) == (24000, 24500, 25500, 26000)


def test_select_condor_hedges_first_and_credit():
    res = select_condor(25000, 600, 300, 50, chain(), quote_fn(), 65, 2.0, 10.0)
    assert isinstance(res, Selection)
    assert [l.role for l in res.legs] == ["LONG_PE", "LONG_CE", "SHORT_PE", "SHORT_CE"]
    assert [l.side for l in res.legs] == [BUY, BUY, SELL, SELL]
    assert res.net_credit_pts > 10 and all(l.qty == 65 for l in res.legs)


def test_select_condor_rejections():
    assert "spread" in select_condor(25000, 600, 300, 50, chain(), quote_fn(spread=3.0), 65, 2.0, 10.0).reason
    assert "net credit" in select_condor(25000, 600, 300, 50, chain(), quote_fn(decay=0.3), 65, 2.0, 10.0).reason
    miss = option_token(24700, "PE")
    r = select_condor(25000, 600, 300, 50, chain(), quote_fn(missing={miss}), 65, 2.0, 10.0)
    assert isinstance(r, Rejection) and "no bid/ask" in r.reason


def snap(vix=14.0, gap=0.2, adv=30, dec=20):
    return PremarketSnapshot(NOW, "NIFTY 50", 25000, 24950, gap, vix, adv, dec)


def test_filters():
    cfg = FilterConfig()
    assert filters.evaluate(snap(), cfg).trade
    assert not filters.evaluate(snap(vix=20.0), cfg).trade           # strictly below 20
    assert not filters.evaluate(snap(vix=None), cfg).trade
    assert not filters.evaluate(snap(gap=-0.9), cfg).trade
    assert not filters.evaluate(snap(adv=46, dec=4), cfg).trade
    assert filters.evaluate(snap(adv=0, dec=0), cfg).trade           # no breadth data: not enforced
    assert filters.evaluate(snap(gap=1.5), FilterConfig(gap_filter=False)).trade


def test_premium_blowout():
    legs = [Leg("LONG_PE", 1, "a", BUY, 65, entry_price=2.0), Leg("LONG_CE", 2, "b", BUY, 65, entry_price=2.0),
            Leg("SHORT_PE", 3, "c", SELL, 65, entry_price=10.0), Leg("SHORT_CE", 4, "d", SELL, 65, entry_price=10.0)]
    pos = Position("IRON_CONDOR", "NIFTY", legs, status="OPEN")       # credit 16
    calc = ChargeCalculator(ChargeRates())
    rule = PremiumBlowout(1.35, 2.0)

    def mark(pe_ask, ce_ask, long_bid=1.0):
        t = {1: tick(1, 1, long_bid, long_bid + .1), 2: tick(2, 1, long_bid, long_bid + .1),
             3: tick(3, pe_ask, pe_ask - .1, pe_ask), 4: tick(4, ce_ask, ce_ask - .1, ce_ask)}
        return RiskContext(NOW, pos, pos.mark(t.get, calc, NOW), 1.0)

    assert rule.check(mark(10, 10)) is None                 # premium 18 < 21.6
    assert rule.check(mark(10, 13)) is None                 # premium 21 < 21.6, no short doubled
    assert "net premium" in rule.check(mark(11, 13))        # premium 22 >= 1.35 x 16
    assert "d at 20.00" in rule.check(mark(2, 20, long_bid=4.0))   # short CE doubled, premium 14


def test_default_exits_are_points_on_gross_pnl():
    cfg = IronCondorConfig(algo_id="iron_condor", name="x")
    assert cfg.exits.pnl_basis == "gross"
    assert (cfg.exits.stop_loss_points, cfg.exits.target_points) == (5.0, 8.0)


def test_config_yaml_uses_points_on_gross_pnl():
    from trading_core.strategy import registry

    _, cfg = registry.load("iron_condor")
    assert cfg.exits.pnl_basis == "gross"
    assert (cfg.exits.stop_loss_points, cfg.exits.target_points) == (5, 8)
