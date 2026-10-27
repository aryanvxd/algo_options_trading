from datetime import date, datetime, timedelta

import numpy as np

from algos.strangle_scalp.entry_signal import PairSignal
from algos.strangle_scalp.pair_selector import Pair, select_pair
from algos.strangle_scalp.settings import PairConfig, SignalConfig
from tests.helpers import option_token, symbol, tick
from trading_core.broker.base import BUY, SELL
from trading_core.clock import IST
from trading_core.kite.instruments import InstrumentInfo
from trading_core.marketdata import greeks
from trading_core.risk.engine import RiskContext
from trading_core.risk.rules import MaxHold, Stall, TrailingStop
from trading_core.strategy import payoff
from trading_core.strategy.position import Leg, Mark, Position

T0 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)
EXPIRY = date(2026, 10, 27)


def leg(role, opt, side, strike, price, qty=65):
    return Leg(role=role, instrument_token=1, tradingsymbol=role, side=side, qty=qty, option_type=opt,
               strike=strike, entry_price=price)


# ---------------------------------------------------------------- payoff

def test_iron_condor_max_loss_is_wing_minus_credit():
    legs = [leg("LONG_PE", "PE", BUY, 24400, 5), leg("SHORT_PE", "PE", SELL, 24700, 20),
            leg("SHORT_CE", "CE", SELL, 25300, 20), leg("LONG_CE", "CE", BUY, 25600, 5)]
    credit = 30
    assert payoff.max_loss(legs) == (300 - credit) * 65
    assert payoff.expiry_pnl(legs, 25000) == credit * 65
    be = payoff.breakevens(legs, list(range(24000, 26050, 50)))
    assert be == [24670.0, 25330.0]


def test_long_strangle_loses_at_most_the_premium_and_naked_short_is_unbounded():
    legs = [leg("LONG_CE", "CE", BUY, 25100, 20), leg("LONG_PE", "PE", BUY, 24900, 20)]
    assert payoff.max_loss(legs) == 40 * 65
    assert payoff.max_loss([leg("SHORT_CE", "CE", SELL, 25100, 20)]) is None


# ---------------------------------------------------------------- exit rules

def ctx_at(seconds, pnl, entry=T0):
    pos = Position("LONG_STRANGLE", "NIFTY", [], entry_time=entry)
    mark = Mark(ts=T0, net_premium=None, gross_pnl=pnl, entry_charges=0, exit_charges=0, complete=True)
    return RiskContext(now=entry + timedelta(seconds=seconds), position=pos, mark=mark, feed_age_s=0.5)


def test_trailing_stop_arms_at_the_activation_level_then_keeps_half_the_peak():
    rule = TrailingStop(300, 0.5, basis="gross")
    assert rule.check(ctx_at(1, 250)) is None
    assert rule.check(ctx_at(2, 100)) is None            # never armed: fell back below 300 first
    assert rule.check(ctx_at(3, 420)) is None
    assert rule.check(ctx_at(4, 220)) is None            # 220 > 210
    assert "peak 420" in rule.check(ctx_at(5, 205))


def test_stall_fires_after_no_new_high_and_resets_on_one():
    rule = Stall(90, basis="gross")
    assert rule.check(ctx_at(0, -50)) is None
    assert rule.check(ctx_at(60, -10)) is None            # new high at 60 s
    assert rule.check(ctx_at(149, -20)) is None
    assert rule.check(ctx_at(150, -20)) is not None


def test_max_hold():
    assert MaxHold(480).check(ctx_at(479, 0)) is None
    assert MaxHold(480).check(ctx_at(480, 0)) is not None


# ---------------------------------------------------------------- signal

def feed_signal(sig, values, fut_step=1.0):
    for i, v in enumerate(values):
        sig.add(T0 + timedelta(seconds=i), ce=v / 2 + i * 0.01, pe=v / 2 - i * 0.01, fut=25000 + i * fut_step)


def test_signal_needs_three_rising_windows_a_big_enough_rise_and_a_futures_move():
    cfg = SignalConfig()
    rising = [40 + 0.1 * i for i in range(16)]            # +1.5 pts over 15 s = 3.75%
    sig = PairSignal(cfg)
    feed_signal(sig, rising)
    st = sig.evaluate(T0 + timedelta(seconds=15), min_move=12)
    assert st.fired and st.windows_up == 3 and st.driver == "CE"
    assert round(st.rise_pct, 2) == 3.75 and st.fut_move == 15

    sig = PairSignal(cfg)
    feed_signal(sig, rising, fut_step=0.5)                # NIFTY only 7.5 pts: noise, not a move
    assert not sig.evaluate(T0, min_move=12).fired

    sig = PairSignal(cfg)
    flat_then_up = [40.0] * 6 + [40 + 0.3 * i for i in range(10)]
    feed_signal(sig, flat_then_up)
    st = sig.evaluate(T0, min_move=12)
    assert not st.fired and st.windows_up == 2            # the oldest window was flat


def test_signal_not_ready_without_history():
    sig = PairSignal(SignalConfig())
    feed_signal(sig, [40, 41, 42])
    st = sig.evaluate(T0, 12)
    assert not st.ready and not st.fired


# ---------------------------------------------------------------- pair selection

def chain(expiry, strikes):
    return [InstrumentInfo(option_token(k, o), symbol(expiry, k, o), "NIFTY", "NFO", "NFO-OPT", o, float(k), expiry,
                           65, 0.05) for k in strikes for o in ("CE", "PE")]


def quotes(prices, spread=0.1, when=T0):
    book = {}
    for (k, o), p in prices.items():
        book[option_token(k, o)] = tick(option_token(k, o), ltp=p, bid=round(p - spread / 2, 2),
                                        ask=round(p + spread / 2, 2), when=when)
    return book.get


def test_expiry_day_pair_is_the_best_matched_pair_in_the_premium_band():
    prices = {(24900, "PE"): 18.0, (24850, "PE"): 9.0, (24950, "PE"): 34.0,
              (25100, "CE"): 21.0, (25150, "CE"): 11.0, (25050, "CE"): 38.0,
              (25000, "CE"): 60.0, (25000, "PE"): 55.0}
    res = select_pair(25000, chain(EXPIRY, range(24850, 25200, 50)), quotes(prices), T0, 0, EXPIRY,
                      PairConfig(), 0.065)
    assert isinstance(res, Pair)
    assert (res.ce.inst.strike, res.pe.inst.strike) == (25100, 24900)
    assert res.value == 39.0


def test_pair_rejected_when_quotes_are_stale_or_unmatched():
    prices = {(24900, "PE"): 15.5, (25100, "CE"): 29.0}
    res = select_pair(25000, chain(EXPIRY, [24900, 25100]), quotes(prices), T0, 0, EXPIRY, PairConfig(), 0.065)
    assert isinstance(res, str) and "not matched" in res
    old = quotes({(24900, "PE"): 20.0, (25100, "CE"): 20.0}, when=T0 - timedelta(seconds=5))
    res = select_pair(25000, chain(EXPIRY, [24900, 25100]), old, T0, 0, EXPIRY, PairConfig(), 0.065)
    assert isinstance(res, str) and "fresh" in res


def test_other_days_pick_each_leg_by_delta():
    expiry = date(2026, 10, 29)                               # two days out
    t = greeks.years_between(T0, expiry)
    strikes = range(24500, 25550, 50)
    prices = {}
    for k in strikes:
        for o in ("CE", "PE"):
            prices[(k, o)] = round(float(greeks.price(25000, k, t, 0.065, 0.13, o == "CE")), 2)
    res = select_pair(25000, chain(expiry, strikes), quotes(prices), T0, 2, expiry, PairConfig(), 0.065)
    assert isinstance(res, Pair)
    for side in (res.ce, res.pe):
        assert 0.25 <= side.delta <= 0.35 and abs(side.iv - 0.13) < 0.01
    # flat vol and a forward close to spot: the same distance either side
    assert res.ce.inst.strike - 25000 == 25000 - res.pe.inst.strike
