"""What market data the backtests can use, and downloading more (docs/design/backtest.md, 5a and 6.7).

``days()`` lists the trading days (days with NIFTY minute data) and, of those, the days a real-price
backtest can trade: the engine's own rule (``History.real_options``) decides, so the pages show
exactly the days a real run will skip as ``missing_options``. Reading only the expiry column of each
saved option file keeps it quick; results are cached until the files change.

``coverage()`` summarises the market-data manifest (what ``algo data status`` prints) with the gaps.
``DataJobs`` runs one ``algo data ...`` download at a time in the background, its output in logs/.
The Kite downloads need the day's login, which you do yourself (``algo login`` / the Log in button).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pyarrow.compute as pc
import pyarrow.parquet as pq
from sqlalchemy import select

from trading_core.backtest.history import History
from trading_core.config import PlatformConfig
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.marketdata.store import MarketStore

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _signature(store: MarketStore, h: History) -> tuple:
    """Changes whenever a day of index or option data is added or rewritten."""
    sig = []
    for dataset, key in (("index_1m", "NIFTY 50"), ("options_1m", h.underlying)):
        for d in store.days(dataset, key, h.source):
            p = store.path(dataset, key, h.source, day=d)
            try:
                sig.append((dataset, d, p.stat().st_mtime_ns))
            except OSError:
                pass
    return tuple(sig)


def days(platform: PlatformConfig) -> dict[str, Any]:
    store = MarketStore(platform.marketdata.dir)
    h = History(store)
    sig = _signature(store, h)
    with _lock:
        if _cache.get("sig") == sig:
            return _cache["out"]
    trading = h.trading_days(date(2000, 1, 1), date(2100, 1, 1))
    tset = set(trading)
    real, nearest = [], {}
    for d in store.days("options_1m", h.underlying, h.source):
        if d not in tset:
            continue
        prev = max((x for x in trading if x < d), default=None)
        expected, _ = h._expiry(d, h._chain(d) or h._chain(prev), tset)
        path = store.path("options_1m", h.underlying, h.source, day=d)
        col = pq.read_table(path, columns=["expiry"])["expiry"]
        expiries = sorted(e for e in pc.unique(col).to_pylist() if e is not None and e >= d)
        if expiries and expiries[0] <= expected:          # the same test as History.real_options
            real.append(d)
            nearest[d.isoformat()] = expiries[0].isoformat()
    out = dict(trading_days=[d.isoformat() for d in trading], real_option_days=[d.isoformat() for d in real],
               real_expiry=nearest, last_day=trading[-1].isoformat() if trading else None)
    with _lock:
        _cache.update(sig=sig, out=out)
    return out


# ---------------------------------------------------------------- coverage per dataset

MAX_GAPS = 60


def _info(g: dict[str, set], holidays: set[date], label: str, gaps: bool = True) -> dict[str, Any]:
    """First and last day stored, days, and the gaps: weekdays with no file that aren't holidays (a stored
    empty day is a holiday too)."""
    ok, empty, bad = g.get("OK", set()), g.get("EMPTY", set()), g.get("bad", set())
    first, last = (min(ok), max(ok)) if ok else (None, None)
    missing = []
    if gaps and first:
        d = first
        while d <= last:
            if d.weekday() < 5 and d not in ok and d not in empty and d not in holidays:
                missing.append(d.isoformat())
            d += timedelta(days=1)
    return dict(label=label, first=first.isoformat() if first else None, last=last.isoformat() if last else None,
                days=len(ok), empty=len(empty), problems=sorted(x.isoformat() for x in bad),
                gaps=missing[:MAX_GAPS], gap_count=len(missing))


def coverage(platform: PlatformConfig, db: Database, now: datetime) -> dict[str, Any]:
    from trading_core.db.migrate import holidays as stored_holidays
    from trading_core.market.calendar import MarketCalendar
    from trading_core.marketdata.manifest import Manifest
    from trading_core.marketdata.sources.kite_options import next_expiry, reminder

    F = m.MarketDataFile
    groups: dict[tuple[str, str, str], dict[str, set]] = defaultdict(lambda: defaultdict(set))
    with db.session() as s:
        for ds, key, src, d, status in s.execute(select(F.dataset, F.partition_key, F.source, F.trade_date, F.status)):
            groups[(ds, key, src)]["OK" if status == "OK" else "EMPTY" if status == "EMPTY" else "bad"].add(d)
    hol = set(stored_holidays(db))

    def g(ds: str, key: str, src: str = "kite") -> dict[str, set]:
        return groups.get((ds, key, src), {})

    index = [_info(g("index_1m", k), hol, label) for k, label in (("NIFTY 50", "NIFTY 50, 1-minute"),
                                                                  ("INDIA VIX", "India VIX, 1-minute"))]
    futures = _info(g("index_1m", "NIFTY FUT"), hol, "NIFTY future, 1-minute (live contract only)")
    bhav = _info(g("options_eod", "NIFTY", "nse"), hol, "Option chains, end of day (NSE bhavcopy)")

    stocks = {k: v for (ds, k, src), v in groups.items() if ds == "equity_1m"}
    counts = {k: len(v.get("OK", set())) for k, v in stocks.items()}
    most = max(counts.values(), default=0)
    firsts = [min(v["OK"]) for v in stocks.values() if v.get("OK")]
    lasts = [max(v["OK"]) for v in stocks.values() if v.get("OK")]
    stock_info = dict(label="The NIFTY 50 stocks, 1-minute", count=len(stocks), days_max=most,
                      days_min=min(counts.values(), default=0),
                      first=min(firsts).isoformat() if firsts else None, last=max(lasts).isoformat() if lasts else None,
                      short=sorted(({"symbol": k, "days": n} for k, n in counts.items() if n < most), key=lambda x: x["days"]))

    today = now.date()
    expiries = []
    for (ds, key, _src), v in groups.items():
        if ds != "options_1m" or ":" not in key:
            continue
        try:
            expiry = date.fromisoformat(key.split(":", 1)[1])
        except ValueError:
            continue
        last = max(v["OK"]) if v.get("OK") else None
        state = "collecting" if expiry >= today else "complete" if last == expiry else "incomplete"
        expiries.append(dict(_info(v, hol, key, gaps=False), expiry=expiry.isoformat(), state=state))
    expiries.sort(key=lambda e: e["expiry"], reverse=True)

    recorded = [_info(g(ds, key, "recorder"), hol, label, gaps=False) for ds, key, label in
                (("index_ticks", "NIFTY 50", "Recorded NIFTY ticks"), ("options_ticks", "NIFTY", "Recorded option ticks"))]
    try:
        note = reminder(db, Manifest(db), "NIFTY", MarketCalendar(sorted(hol)), now)
        nxt = next_expiry(db, "NIFTY", today)
    except Exception:  # noqa: BLE001 - the reminder is a nicety
        note, nxt = None, None
    return dict(now=now.isoformat(timespec="seconds"), index=index, futures=futures, stocks=stock_info, bhavcopy=bhav,
                expiries=expiries, recorded=recorded, reminder=note, next_expiry=nxt.isoformat() if nxt else None,
                downloads={k: {kk: vv for kk, vv in d.items() if kk not in ("args", "key")} for k, d in DOWNLOADS.items()})


# ---------------------------------------------------------------- downloads

DOWNLOADS: dict[str, dict[str, Any]] = {
    "options": dict(args=["data", "options"], kite=True, needs_from=False, label="Real option candles",
                    help="The nearest expiries' 1-minute candles, plus index, future and stocks. Run it each evening."),
    "index": dict(args=["data", "index"], kite=True, needs_from=True, key=("index_1m", "NIFTY 50", "kite"),
                  label="NIFTY 50 and India VIX", help="1-minute index and VIX history."),
    "futures": dict(args=["data", "futures"], kite=True, needs_from=True, key=("index_1m", "NIFTY FUT", "kite"),
                    label="NIFTY future", help="1-minute near-month future with volume (Kite serves only the live contract)."),
    "constituents-1m": dict(args=["data", "constituents-1m"], kite=True, needs_from=True, key=("equity_1m", None, "kite"),
                            label="The 50 stocks", help="1-minute history of the NIFTY 50 stocks (breadth, VWAP)."),
    "bhavcopy": dict(args=["data", "bhavcopy"], kite=False, needs_from=True, key=("options_eod", "NIFTY", "nse"),
                     label="Option chains (bhavcopy)", help="NSE end-of-day option chains: expiries, strikes, IV. No login needed."),
}


class DataError(Exception):
    """A download the dashboard refuses, with the reason shown to the user."""


def _last_stored(db: Database, key: tuple) -> date | None:
    ds, pk, src = key
    F = m.MarketDataFile
    q = select(F.trade_date).where(F.dataset == ds, F.source == src, F.status == "OK")
    if pk:
        q = q.where(F.partition_key == pk)
    with db.session() as s:
        return s.scalar(q.order_by(F.trade_date.desc()).limit(1))


def spawn_download(platform: PlatformConfig, args: list[str], log_path) -> subprocess.Popen:
    """``algo data ...`` in the background, no window, output in logs/data-<dataset>-<time>.log."""
    cmd = [sys.executable, "-m", "trading_core", *args]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_path, "a", encoding="utf-8")                      # noqa: SIM115 - the child owns it
    out.write(f"$ algo {' '.join(args)}\n")
    out.flush()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=platform.root, env=env, stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags)


class DataJobs:
    """One download at a time; the last one's state and log stay visible until the next starts."""

    def __init__(self, platform: PlatformConfig, spawn: Callable[..., Any] = spawn_download):
        self.platform, self.spawn = platform, spawn
        self.proc: Any = None
        self.job: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def status(self, lines: int = 60) -> dict[str, Any]:
        with self._lock:
            if self.job is None:
                return dict(job=None)
            code = self.proc.poll() if self.proc is not None else self.job.get("exit_code")
            self.job["exit_code"] = code
            job = dict(self.job, running=code is None)
        try:
            with open(job["log"], encoding="utf-8", errors="replace") as f:
                job["lines"] = [x.rstrip("\n") for x in f.readlines()[-lines:]]
        except OSError:
            job["lines"] = []
        return dict(job=job)

    def start(self, db: Database, dataset: str, start: str | None, kite_ok: bool, now: datetime) -> dict[str, Any]:
        spec = DOWNLOADS.get(dataset)
        if spec is None:
            raise DataError(f"unknown dataset {dataset!r}; one of {', '.join(DOWNLOADS)}")
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                raise DataError(f"{self.job['label']} is still downloading: wait for it to finish")
            if spec["kite"] and not kite_ok:
                raise DataError("log in to Kite first (the Log in button, or `algo login`)")
            args = list(spec["args"])
            if spec["needs_from"]:
                if start:
                    try:
                        date.fromisoformat(start)
                    except ValueError:
                        raise DataError("from must be a date (YYYY-MM-DD)") from None
                else:                                # stored days are skipped: start at the last one
                    last = _last_stored(db, spec["key"])
                    start = (last or now.date() - timedelta(days=59)).isoformat()
                args += ["--from", start]
            log = self.platform.logs_dir / f"data-{dataset}-{now:%Y%m%d-%H%M%S}.log"
            self.proc = self.spawn(self.platform, args, log)
            self.job = dict(dataset=dataset, label=spec["label"], args=args, started_at=now.isoformat(timespec="seconds"),
                            log=str(log), pid=self.proc.pid, exit_code=None)
            return dict(self.job, running=True)
