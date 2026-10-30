from datetime import date, datetime, time, timedelta

import pytest

from algos.directional_scalp.bias import CE, PE, BiasTracker, score
from algos.directional_scalp.settings import BiasConfig
from tests.helpers import tick
from trading_core.clock import IST
from trading_core.db.repo import Journal
from trading_core.kite.instruments import InstrumentInfo, filter_rows
from trading_core.market.breadth import Breadth, Member, load_weights, members, prev_closes_from_quotes
from trading_core.marketdata.renko import DOWN, UP, MinuteBars, Renko, Vwap

T0 = datetime(2026, 11, 2, 9, 15, tzinfo=IST)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_renko_continues_one_brick_and_reverses_after_two():
    r = Renko(10)
    assert r.add(at(1), 25000) == []                       # anchor
    assert r.add(at(2), 25009) == []
    [b] = r.add(at(3), 25012)
    assert (b.direction, b.open, b.close) == (UP, 25000, 25010)
    assert [x.close for x in r.add(at(4), 25031)] == [25020, 25030]   # one close, two bricks
    assert r.add(at(5), 25011) == []                      # 19 back: not yet a reversal
    assert r.reversal_level == 25010                      # last brick 25020->25030: open 25020 - 10
    [d] = r.add(at(6), 25010)
    assert (d.direction, d.open, d.close) == (DOWN, 25020, 25010)
    assert r.run(DOWN) == 1 and r.run(UP) == 0
    assert r.run(UP, after=at(3)) == 0
    r.add(at(7), 24990)                                   # two more down bricks, at 09:22
    assert r.run(DOWN) == 3 and r.run(DOWN, after=at(6)) == 2  # only bricks formed after 09:21 count


def test_minute_bars_close_on_the_next_minute_or_after_the_grace():
    bars = MinuteBars(grace_s=2)
    assert bars.add(at(0) + timedelta(seconds=5), 100) == []
    bars.add(at(0) + timedelta(seconds=30), 104)
    bars.add(at(0) + timedelta(seconds=55), 101)
    assert bars.flush(at(1) + timedelta(seconds=1)) == []  # inside the grace
    [b] = bars.add(at(1) + timedelta(seconds=1), 102)    # a price in the next minute closes it
    assert (b.ts, b.open, b.high, b.low, b.close) == (at(0), 100, 104, 100, 101)
    [b2] = bars.flush(at(2) + timedelta(seconds=3))      # no tick at all: closed after the grace
    assert b2.close == 102
    bars.seed(at(5))
    assert bars.add(at(5) + timedelta(seconds=10), 99) == [] and bars.current is None   # warm-up covered it


def test_vwap_from_warm_up_candles_then_cumulative_tick_volume():
    v = Vwap()
    v.add_bar(high=102, low=98, close=100, volume=1000)   # typical 100
    v.add_tick(200, 5000)                                 # baseline only: already in the candles
    assert v.value == 100
    v.add_tick(110, 6000)                                 # 1000 more at 110
    v.add_tick(110, 6000)                                 # the same tick again adds nothing
    assert v.value == 105


def test_weighted_breadth_counts_index_weight_not_heads():
    stocks = [Member(1, "HDFCBANK", 40, 100), Member(2, "SMALL1", 30, 100), Member(3, "SMALL2", 30, 100)]
    quotes = {1: tick(1, ltp=99), 2: tick(2, ltp=101), 3: tick(3, ltp=100.005)}
    r = Breadth(stocks).read(quotes.get)
    assert (r.advances, r.declines, r.unchanged) == (1, 1, 1)
    assert r.weighted == -10.0                             # 30% of the index up, 40% down
    assert r.coverage == 100.0 and r.leaders[0] == ("HDFCBANK", -1.0)
    assert Breadth(stocks, min_coverage=60).read({1: quotes[1]}.get) is None   # only 40% has a price


def test_breadth_members_reweight_the_stocks_that_have_data(platform):
    w = load_weights(platform)
    assert len(w) == 50 and sum(w.values()) == pytest.approx(100) and "WIPRO" not in w and w["HDFCBANK"] > w["BSE"]
    info = {s: InstrumentInfo(i, s, s, "NSE", "NSE", "EQ", None, None, 1, 0.05)
            for i, s in enumerate(["HDFCBANK", "RELIANCE", "NOPRICE"], start=1)}
    ms = members(info, w, {1: 1000.0, 2: 1400.0})
    assert [m.symbol for m in ms] == ["HDFCBANK", "RELIANCE"] and sum(m.weight for m in ms) == pytest.approx(100)
    quotes = {"NSE:HDFCBANK": {"ohlc": {"close": 1000.0}}, "NSE:RELIANCE": {"ohlc": {"close": 0}}}
    assert prev_closes_from_quotes(quotes, info) == {1: ("HDFCBANK", 1000.0)}


def test_instrument_snapshot_keeps_only_the_listed_stocks():
    nse = [dict(segment="INDICES", tradingsymbol="NIFTY 50", instrument_type="EQ"),
           dict(segment="NSE", tradingsymbol="HDFCBANK", instrument_type="EQ"),
           dict(segment="NSE", tradingsymbol="ZOMATO-BE", instrument_type="EQ"),
           dict(segment="NSE", tradingsymbol="HDFCBANK", instrument_type="BOND")]
    rows = filter_rows([], nse, ["NIFTY"], {"HDFCBANK"})
    assert [(r["segment"], r["tradingsymbol"], r["instrument_type"]) for r in rows] == [
        ("INDICES", "NIFTY 50", "EQ"), ("NSE", "HDFCBANK", "EQ")]


def test_journal_keeps_the_days_prev_closes_and_candles(db):
    j, d = Journal(db), date(2026, 11, 2)
    assert j.save_prev_closes(d, {1: ("HDFCBANK", 1000.0), 2: ("ITC", 400.0)}, T0) == 2
    j.save_prev_closes(d, {1: ("HDFCBANK", 1001.0)}, T0)                  # replaced, not duplicated
    assert j.prev_closes(d) == {1: 1001.0, 2: 400.0}
    bars = [dict(ts=at(i), open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(3)]
    j.save_bars(d, 7, bars)
    j.save_bars(d, 7, bars[2:])
    assert [b.ts for b in j.bars(d, 7)] == [at(0), at(1), at(2)]
    assert [b.ts for b in j.bars(d, 7, before=at(2))] == [at(0), at(1)]


BIAS = BiasConfig()


def test_bias_score_parts_and_the_breadth_veto():
    r = score(BIAS, T0, weighted_ad=40, skew_ratio=1.2, fut_minus_vwap=15)
    assert (r.b1, r.b2, r.b3, r.score) == (1.0, 0.0, 1.0, 0.7)
    assert score(BIAS, T0, 20, 1.64, None).b2 == -1.0                     # 30 Oct: puts much richer
    assert score(BIAS, T0, None, 0.9, 20).score is None                   # no breadth -> no score
    t = BiasTracker(BIAS)
    t.update(score(BIAS, T0, -10, 0.8, 30))                               # bullish 0.56, breadth negative
    assert t.raw_side(t.last) is None


def test_bias_side_needs_the_hold_time_and_flips_reset_it():
    t = BiasTracker(BIAS)
    for s in range(0, 125, 5):
        t.update(score(BIAS, T0 + timedelta(seconds=s), 40, 1.0, 10))
    assert t.side(T0 + timedelta(seconds=115)) is None and t.side(T0 + timedelta(seconds=120)) == CE
    t.update(score(BIAS, T0 + timedelta(seconds=130), -40, 1.6, -15))
    assert t.raw_side(t.last) == PE and t.side(T0 + timedelta(seconds=200)) is None
    assert t.against(CE) and not t.against(PE)
