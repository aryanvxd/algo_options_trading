"""Backtests on a synthetic minute-history store: no Kite, no network."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time

import pyarrow as pa
import pytest
from sqlalchemy import select

from trading_core.backtest.engine import run_backtest
from trading_core.backtest.feed import BacktestFeed, OptionSpec
from trading_core.backtest.history import Bars, History
from trading_core.clock import IST, SimClock
from trading_core.config import load_universe
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.marketdata import greeks
from trading_core.marketdata.store import MarketStore

PREV, FLAT, RISING = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)   # Mon, Tue (expiry), Wed


def minutes() -> list[datetime]:
    return [datetime(2000, 1, 1, 9, 15) .replace(hour=9 + (15 + i) // 60, minute=(15 + i) % 60) for i in range(375)]


def write(store: MarketStore, dataset: str, key: str, d: date, closes, volume: float = 0.0, wick: float = 1) -> None:
    ts = [datetime.combine(d, t.time()) for t in minutes()]
    o = [closes[0]] + list(closes[:-1])
    store.write_day(dataset, key, d, "kite", pa.table({
        "ts": ts, "trade_date": [d] * len(ts), "symbol": [key] * len(ts), "open": o,
        "high": [max(a, b) + wick for a, b in zip(o, closes)], "low": [min(a, b) - wick for a, b in zip(o, closes)],
        "close": list(closes), "volume": [volume] * len(ts), "source": ["kite"] * len(ts)}))


def seed_history(platform, rising_rate: float = 2.5) -> list[str]:
    """Mon flat; Tue flat; Wed flat at 25000, then +rate pts/min from 09:50 to 10:50, stocks +0.6% all day."""
    store = MarketStore(platform.marketdata.dir)
    symbols = [k.split(":", 1)[1] for k in load_universe(platform, "nifty50")]
    for d in (PREV, FLAT, RISING):
        if d == RISING:
            spot = [25000 + rising_rate * min(max(i - 35, 0), 60) for i in range(375)]
            stock = 1006.0
        else:
            spot, stock = [25000.0] * 375, 1000.0
        write(store, "index_1m", "NIFTY 50", d, spot)
        write(store, "index_1m", "INDIA VIX", d, [14.0] * 375, wick=0.5)    # IV follows the minute closes
        write(store, "index_1m", "NIFTY FUT", d, [s + 20 for s in spot], volume=1000)
        for s in symbols:
            write(store, "equity_1m", s, d, [stock] * 375, volume=500)
    return symbols


def test_feed_walks_the_ohlc_path_and_prices_options_with_black76(platform):
    seed_history(platform)
    h = History(MarketStore(platform.marketdata.dir))
    data = h.load(RISING, FLAT, [], {PREV, FLAT, RISING})
    assert data.expiry == date(2026, 11, 3) and data.expiry_source == "rule"      # Tuesday weeklies
    assert data.vix_ref == 14.0 and data.smile.empty
    clock = SimClock(datetime(2026, 10, 28, 10, 0, 0, tzinfo=IST))
    feed = BacktestFeed(data, clock, {256265: data.index, 264969: data.vix, 9: data.fut},
                        {1: OptionSpec(25000, "CE", data.expiry)}, 256265, 264969, r=0.065)
    b = data.index
    i = list(b.minutes).index(10 * 60)
    seen = []
    for sec in (0, 15, 30, 45):
        clock._now = clock._now.replace(second=sec)
        seen.append(feed.latest(256265).ltp)
    assert seen == [b.open[i], b.low[i], b.high[i], b.close[i]]                   # rising bar: O, L, H, C
    assert feed.latest(9).volume == int(1000 * i + 1000)                        # cumulative day volume
    opt = feed.latest(1)
    t = greeks.years_between(clock.now(), data.expiry)
    model = float(greeks.price(b.close[i] * math.exp(0.065 * t), 25000, t, 0.065, 0.14, True))
    assert abs(opt.ltp - model) < 0.06 and opt.bid < opt.ltp < opt.ask
    assert feed.last_tick_age(clock.now()) == 0.5
    clock._now = datetime(2026, 10, 28, 16, 0, tzinfo=IST)
    assert feed.last_tick_age(clock.now()) > 60                                 # after the data: stale


def test_backtest_runs_the_real_algo_and_writes_a_summary(platform, db):
    seed_history(platform)
    folder = run_backtest(platform, "directional_scalp", FLAT, RISING, name="t1", progress=lambda m: None)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done" and status["done"] == 2
    t = summary["totals"]
    assert t["days"] == 2 and t["days_traded"] == 1 and t["trades"] >= 1
    assert t["net"] > 0 and summary["by_side"]["CE"]["trades"] == t["trades"]
    assert set(summary["by_dte"]) == {"4"}                                      # Wed -> Tue 3 Nov
    assert summary["equity"][-1]["cum"] == t["net"]
    assert summary["coverage"]["with_futures"] == 2 and summary["coverage"]["stocks_min"] == 50
    assert summary["meta"]["backtest_overrides"]["bias"]["w_skew"] == 0.0
    assert any("no entry" in k for k in summary["skips"])                     # the flat Tuesday

    bt = Database(f"sqlite:///{(folder / 'backtest.db').as_posix()}")
    with bt.session() as s:
        [first, *_] = s.scalars(select(m.Position).order_by(m.Position.position_id)).all()
        assert first.params["bias"]["b2"] is not None and first.params["side"] == "CE"
        assert first.params["bias"]["b3"] == 0.0                         # stocks flat at their VWAP all day
        assert first.params["vwap"] == pytest.approx(first.params["spot"])
    with db.session() as s:                                                     # the platform DB is untouched
        assert s.scalars(select(m.Position)).all() == []

    with pytest.raises(ValueError, match="already exists"):
        run_backtest(platform, "directional_scalp", FLAT, RISING, name="t1", progress=lambda m: None)


def test_settings_can_be_overridden_and_bad_ones_fail_fast(platform):
    seed_history(platform)
    folder = run_backtest(platform, "directional_scalp", RISING, RISING, name="wide",
                          overrides={"trigger": {"brick_pts": 40}}, progress=lambda m: None)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["overrides"] == {"trigger": {"brick_pts": 40}}
    assert summary["totals"]["trades"] <= 1                                   # 40-pt bricks: two need 80 pts
    with pytest.raises(Exception):
        run_backtest(platform, "directional_scalp", RISING, RISING, name="bad",
                     overrides={"trigger": {"no_such_setting": 1}}, progress=lambda m: None)


def test_cli_set_parsing():
    from trading_core.cli import parse_sets

    assert parse_sets(["trigger.brick_pts=12", "bias.threshold=0.6", "split_lots=false"]) == {
        "trigger": {"brick_pts": 12}, "bias": {"threshold": 0.6}, "split_lots": False}
    with pytest.raises(ValueError):
        parse_sets(["nonsense"])
