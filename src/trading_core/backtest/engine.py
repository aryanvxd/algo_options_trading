"""``algo backtest``: run an algo, day by day, over minute history (docs/design/backtest.md).

Each backtest lives in ``data/backtests/<name>/``:
  backtest.db   runs, trades, checks, events (the live schema, so the dashboard can read it)
  status.json   progress while it runs (day N of M), then done / failed
  summary.json  metrics, settings and data coverage (``summary.summarize``)
"""

from __future__ import annotations

import importlib
import json
import logging
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from trading_core.backtest.feed import BacktestFeed, OptionSpec
from trading_core.backtest.history import INDEX, VIX, DayData, History
from trading_core.backtest.summary import summarize
from trading_core.clock import SimClock, ist
from trading_core.config import PlatformConfig, deep_merge, load_universe
from trading_core.db.engine import Database
from trading_core.db.repo import Journal
from trading_core.kite.instruments import InstrumentStore
from trading_core.market.calendar import MarketCalendar
from trading_core.marketdata.store import MarketStore
from trading_core.strategy import registry
from trading_core.strategy.bootstrap import REPLAY_START, run_algo

log = logging.getLogger(__name__)

INDEX_TOKEN, VIX_TOKEN, FUT_TOKEN = 256265, 264969, 9_000_001
STOCK_TOKEN_BASE, OPTION_TOKEN_BASE = 8_000_000, 50_000_000
QUIET = ("algo", "trading_core.strategy", "trading_core.broker", "trading_core.feed", "trading_core.market")


def backtest_overrides(algo_id: str) -> dict[str, Any]:
    """What an algo changes for backtests (``BACKTEST_OVERRIDES`` in its algo.py), e.g. switching off
    a signal the history can't show."""
    module = importlib.import_module(f"{registry.PACKAGE}.{algo_id}.algo")
    return dict(getattr(module, "BACKTEST_OVERRIDES", {}) or {})


def bt_platform(platform: PlatformConfig, folder: Path) -> PlatformConfig:
    """The platform config with the database, reports and logs inside the backtest's folder."""
    return PlatformConfig(**{**platform.model_dump(), "root": platform.root,
                             "db_url": f"sqlite:///{(folder / 'backtest.db').as_posix()}",
                             "data_dir": folder, "logs_dir": folder / "logs", "calendar_autofetch": False,
                             "marketdata": {**platform.marketdata.model_dump(), "dir": platform.marketdata.dir}})


def default_name(algo_id: str, start: date, end: date) -> str:
    return f"{algo_id}_{start:%Y%m%d}_{end:%Y%m%d}_{datetime.now():%H%M%S}"


def run_backtest(platform: PlatformConfig, algo_id: str, start: date, end: date, *,
                 overrides: dict[str, Any] | None = None, name: str | None = None, lot_size: int = 65,
                 progress: Callable[[str], None] = print, history: History | None = None) -> Path:
    name = name or default_name(algo_id, start, end)
    folder = platform.data_dir / "backtests" / name
    if (folder / "backtest.db").exists():
        raise ValueError(f"backtest '{name}' already exists ({folder}); pick another --name")
    folder.mkdir(parents=True, exist_ok=True)
    bt = bt_platform(platform, folder)
    db = Database(bt.db_url)
    db.create_all()

    history = history or History(MarketStore(platform.marketdata.dir))
    lookback = start - timedelta(days=10)                       # the day before ``start`` gives its prev closes
    trading = history.trading_days(lookback, end)
    days = [d for d in trading if start <= d <= end]
    weekdays = [lookback + timedelta(days=i) for i in range((end - lookback).days + 1)]
    calendar = MarketCalendar([d for d in weekdays if d.weekday() < 5 and d not in set(trading)])
    effective = deep_merge(backtest_overrides(algo_id), overrides or {})
    _, cfg = registry.load(algo_id, effective)                  # fail fast on a bad --set
    symbols = [k.split(":", 1)[1] for k in load_universe(platform, "nifty50")]

    meta = dict(name=name, algo_id=algo_id, version=cfg.version, start=str(start), end=str(end), days=len(days),
                lot_size=lot_size, overrides=overrides or {}, backtest_overrides=backtest_overrides(algo_id),
                started_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    _status(folder, meta, "running", 0)
    if not days:
        _status(folder, meta, "failed", 0, error=f"no NIFTY minute data between {start} and {end} "
                                                 "(run `algo data index --from ...`)")
        raise ValueError(f"no NIFTY minute data between {start} and {end}")

    coverage: dict[str, dict[str, Any]] = {}
    quiet = {n: logging.getLogger(n).level for n in QUIET}
    for n in QUIET:
        logging.getLogger(n).setLevel(logging.WARNING)
    t0, n = _time.monotonic(), 0
    try:
        for n, d in enumerate(days, start=1):
            prev = max((x for x in trading if x < d), default=None)
            data = history.load(d, prev, symbols, set(trading))
            if data is None:
                coverage[str(d)] = dict(missing="index")
                continue
            coverage[str(d)] = data.coverage()
            clock = SimClock(ist(d, REPLAY_START))
            feed = _prepare_day(db, bt, data, clock, lot_size)
            run_algo(bt, db, algo_id, replay_date=d, overrides=effective, calendar=calendar, clock=clock, feed=feed)
            _status(folder, meta, "running", n)
            if n == 1 or n % 20 == 0 or n == len(days):
                progress(f"{d}  day {n}/{len(days)}  ({_time.monotonic() - t0:.0f}s)")
    except Exception as e:
        _status(folder, meta, "failed", n, error=repr(e))
        raise
    finally:
        for name_, level in quiet.items():
            logging.getLogger(name_).setLevel(level)

    summary = summarize(db, meta, coverage)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _status(folder, meta, "done", len(days), elapsed_s=round(_time.monotonic() - t0))
    return folder


def _prepare_day(db: Database, bt: PlatformConfig, data: DayData, clock: SimClock, lot_size: int) -> BacktestFeed:
    """Instrument snapshot and previous closes for the day (as a live run would have stored), and its feed."""
    d = data.day
    rows: list[dict[str, Any]] = [
        _row(INDEX_TOKEN, INDEX, INDEX, "NSE", "INDICES", "EQ"),
        _row(VIX_TOKEN, VIX, VIX, "NSE", "INDICES", "EQ"),
    ]
    bars = {INDEX_TOKEN: data.index}
    if data.vix is not None:
        bars[VIX_TOKEN] = data.vix
    if data.fut is not None:
        rows.append(_row(FUT_TOKEN, "NIFTYFUT", "NIFTY", "NFO", "NFO-FUT", "FUT", expiry=d + timedelta(days=60),
                         lot_size=lot_size))
        bars[FUT_TOKEN] = data.fut
    closes = {}
    for i, (sym, b) in enumerate(sorted(data.stocks.items())):
        tok = STOCK_TOKEN_BASE + i
        rows.append(_row(tok, sym, sym, "NSE", "NSE", "EQ"))
        bars[tok] = b
        closes[tok] = (sym, data.prev_closes[sym])
    options = {}
    for i, k in enumerate(data.strikes):
        for j, opt in enumerate(("CE", "PE")):
            tok = OPTION_TOKEN_BASE + i * 2 + j
            rows.append(_row(tok, f"NIFTY{data.expiry:%y%m%d}{int(k)}{opt}", "NIFTY", "NFO", "NFO-OPT", opt,
                             strike=k, expiry=data.expiry, lot_size=lot_size))
            options[tok] = OptionSpec(k, opt, data.expiry)
    InstrumentStore(db, bt.instruments).save_snapshot(d, rows)
    Journal(db).save_prev_closes(d, closes, ist(d, REPLAY_START), source="backtest")
    return BacktestFeed(data, clock, bars, options, INDEX_TOKEN, VIX_TOKEN, bt.marketdata.risk_free_rate,
                        no_wicks=[tok for tok, _ in closes.items()])


def _row(token: int, symbol: str, name: str, exchange: str, segment: str, itype: str, strike: float = 0,
         expiry: date | None = None, lot_size: int = 1) -> dict[str, Any]:
    return dict(instrument_token=token, exchange_token=None, tradingsymbol=symbol, name=name, exchange=exchange,
                segment=segment, instrument_type=itype, strike=strike, expiry=expiry or "", lot_size=lot_size,
                tick_size=0.05)


def _status(folder: Path, meta: dict[str, Any], state: str, done: int, **extra: Any) -> None:
    body = {**meta, "state": state, "done": done, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **extra}
    tmp = folder / "status.json.tmp"
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(folder / "status.json")
