"""Backtests on real option candles (``--options real``): no Kite, no network."""

from __future__ import annotations

import json
from datetime import date, datetime

import pyarrow as pa
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.backtest.test_backtest import FLAT, PREV, RISING, minutes, seed_history
from tests.helpers import option_price
from trading_core.backtest.engine import run_backtest
from trading_core.backtest.feed import BacktestFeed, half_spread
from trading_core.backtest.history import History
from trading_core.clock import IST, SimClock
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.marketdata.store import MarketStore

EXPIRY = date(2026, 11, 3)


def write_candles(platform, d: date, expiry: date, lo: int = 24300, hi: int = 25700, decay_to: float = 0.3,
                  skip_before: int = 0) -> None:
    rows = []
    ts = [datetime.combine(d, t.time()) for t in minutes()]
    for k in range(lo, hi + 50, 50):
        for o in ("CE", "PE"):
            for i, t in enumerate(ts):
                if i < skip_before:
                    continue
                p = option_price(o, k, 25000.0, 1.0 - (1.0 - decay_to) * i / 375)
                rows.append(dict(ts=t, trade_date=d, underlying="NIFTY", expiry=expiry, strike=float(k), opt_type=o,
                                 open=p, high=p, low=p, close=p, volume=10, oi=1000 if o == "CE" else 1200,
                                 filled=False, spot=25000.0, source="kite"))
    MarketStore(platform.marketdata.dir).write_day("options_1m", "NIFTY", d, "kite", pa.Table.from_pylist(rows))


def history(platform) -> History:
    return History(MarketStore(platform.marketdata.dir))


def load_real(platform, d=RISING, prev=FLAT):
    return history(platform).load(d, prev, [], {PREV, FLAT, RISING}, real=True)


def feed_at(data, h: int, mi: int, s: int = 0):
    clock = SimClock(datetime(2026, 10, 28, h, mi, s, tzinfo=IST))
    tok = {(25000.0, "CE"): 1}
    return BacktestFeed(data, clock, {256265: data.index}, {}, 256265, None, r=0.065,
                        real={t: data.real[k] for k, t in tok.items()}), clock


def test_half_spread_follows_the_measured_buckets():
    assert [half_spread(p) for p in (2, 20, 60, 150)] == [0.025, 0.05, 0.1, 0.5]


def test_quote_is_the_last_finished_minute_never_the_current_one(platform):
    seed_history(platform)
    write_candles(platform, RISING, EXPIRY)
    data = load_real(platform)
    bars = data.real[(25000.0, "CE")]
    i = list(bars.minutes).index(10 * 60)
    feed, clock = feed_at(data, 10, 0, 30)
    t = feed.latest(1)
    assert t.ltp == bars.close[i - 1]                                # 10:00 candle is still forming
    clock._now = clock._now.replace(minute=1, second=0)
    t = feed.latest(1)
    assert t.ltp == bars.close[i]
    assert t.bid == pytest.approx(t.ltp - half_spread(t.ltp)) and t.ask == pytest.approx(t.ltp + half_spread(t.ltp))
    assert t.oi == 1000 and t.has_depth
    assert feed.latest(2) is None                                    # no candles for it: no quote, never a model


def test_no_quote_before_the_contracts_first_candle(platform):
    seed_history(platform)
    write_candles(platform, RISING, EXPIRY, skip_before=60)          # first trade at 10:15
    feed, clock = feed_at(load_real(platform), 10, 0)
    assert feed.latest(1) is None
    clock._now = clock._now.replace(minute=16)
    assert feed.latest(1) is not None


def test_the_nearest_expiry_in_the_file_is_used(platform):
    seed_history(platform)
    store = MarketStore(platform.marketdata.dir)
    write_candles(platform, RISING, date(2026, 11, 2))               # moved a day early by a holiday
    near = store.read("options_1m", "NIFTY", "kite", day=RISING)
    write_candles(platform, RISING, date(2026, 11, 10))
    far = store.read("options_1m", "NIFTY", "kite", day=RISING)
    store.write_day("options_1m", "NIFTY", RISING, "kite", pa.concat_tables([near, far]))
    data = load_real(platform)
    assert data.expiry == date(2026, 11, 2) and data.real and data.strikes[0] == 24300


def test_a_day_without_its_nearest_expiry_has_no_real_data(platform):
    seed_history(platform)
    write_candles(platform, FLAT, EXPIRY)      # 27 Oct's own expiry was never saved; only the next week's
    assert load_real(platform, FLAT, PREV).real is None
    assert load_real(platform).real is None    # and no file at all for the 28th


def test_backtest_trades_on_real_candles_and_skips_days_it_cant_trust(platform, db):
    seed_history(platform)
    write_candles(platform, FLAT, EXPIRY)      # wrong week: must not trade this day
    write_candles(platform, RISING, EXPIRY)
    folder = run_backtest(platform, "iron_condor_balanced", FLAT, RISING, name="real", progress=lambda _m: None,
                          overrides={"exits": {"stop_by_dte": {0: 999}, "target_by_dte": {0: 999},
                                               "premium_blowout_mult": 99, "short_leg_mult": 99}},
                          option_prices="real")
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["option_prices"] == "real"
    assert summary["coverage"]["missing_options"] == [str(FLAT)] and summary["coverage"]["missing_index"] == []
    assert summary["limits"][0].startswith("Option prices are real")
    assert summary["totals"]["trades"] == 1 and set(summary["by_dte"]) == {"4"}
    assert summary["totals"]["gross"] > 0                            # premium decays all day

    bt = Database(f"sqlite:///{(folder / 'backtest.db').as_posix()}")
    with bt.session() as s:
        pos = s.scalars(select(m.Position).options(selectinload(m.Position.legs))).one()
        check = s.scalars(select(m.RunCheck).where(m.RunCheck.check_name == "pcr")).first()
    assert check.value == pytest.approx(1.2)                         # real OI reaches PCR
    legs = {l.role: l for l in pos.legs}
    assert legs["SHORT_PE"].strike == 24700 and legs["SHORT_CE"].strike == 25300
    i = 29                                                           # 09:44, the last minute finished at 09:45
    mid = option_price("PE", 24700, 25000.0, 1.0 - 0.7 * i / 375)
    assert legs["SHORT_PE"].entry_price == pytest.approx(mid - half_spread(mid) - 0.1, abs=0.06)


def test_model_prices_are_still_the_default(platform):
    seed_history(platform)
    folder = run_backtest(platform, "iron_condor_balanced", RISING, RISING, name="m", progress=lambda _m: None)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["option_prices"] == "model"
    assert summary["limits"][0].startswith("Option prices are modelled") and not summary["coverage"]["missing_options"]


def test_backtest_cli_flag():
    from trading_core.cli import build_parser

    p = build_parser()
    args = ["backtest", "--algo", "iron_condor_balanced", "--from", "2026-11-04"]
    assert p.parse_args(args).options == "model"
    assert p.parse_args(args + ["--options", "real"]).options == "real"
    assert p.parse_args(["data", "options"]).start is None
