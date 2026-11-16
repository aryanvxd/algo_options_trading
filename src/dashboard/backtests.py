"""Backtest folders for the dashboard (docs/design/backtest.md, section 6).

Thin on purpose: this lists and reads ``data/backtests/<name>/`` (status.json, summary.json,
request.json, backtest.db, run.log), moves a folder to ``.trash``, and queues new runs of the existing
``algo backtest``. Building the form, diffing settings and charting happen in the page. The per-run
trade log, journal and trade detail are the strategy pages' own queries (``strategy_queries``) pointed
at the run's ``backtest.db``.

The queue is the folders themselves: a new run gets its folder at once with ``state: queued``; a worker
thread in the dashboard starts the oldest one whenever no backtest is running, as a background process
with below-normal priority (this laptop also runs the live paper algos).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from dashboard import control
from trading_core.config import PlatformConfig, deep_merge, int_keys
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.strategy import registry

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
STALE_AFTER_S = 15 * 60          # a "running" folder without a pid and no update for this long has stopped
TRASH = ".trash"
TOTALS = ("trades", "net", "win_rate", "profit_factor", "expectancy", "max_drawdown", "days", "days_traded",
          "days_skipped", "wins", "losses")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class BacktestError(Exception):
    """A request the dashboard refuses, with the reason shown to the user."""


def root(platform: PlatformConfig) -> Path:
    return platform.data_dir / "backtests"


def folder(platform: PlatformConfig, name: str) -> Path | None:
    """The run's folder, or None for an unknown or unsafe name (no paths, no dot folders)."""
    if not NAME_RE.match(name or ""):
        return None
    path = root(platform) / name
    return path if path.is_dir() and (path / "status.json").exists() else None


# ---------------------------------------------------------------- small cached file reads

_json_cache: dict[Path, tuple[int, int, Any]] = {}
_cache_lock = threading.Lock()


def _read_json(path: Path) -> Any:
    """Parsed JSON, cached by modification time and size, so a list of 150 folders stays fast."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _json_cache.get(path)
        if hit and hit[:2] == key:
            return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _cache_lock:
        _json_cache[path] = (*key, data)
    return data


# ---------------------------------------------------------------- state

def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _age_s(ts: datetime | None, now: datetime) -> float | None:
    """Seconds from ``ts`` to ``now``; a naive time is taken to be in ``now``'s zone."""
    if ts is None:
        return None
    if ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    elif ts.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=ts.tzinfo)
    return (now - ts).total_seconds()


def state_of(status: dict[str, Any], now: datetime) -> tuple[str, str]:
    """(state, label): queued / running / done / failed / stale, and a short text for people."""
    state = status.get("state") or "unknown"
    done, days = status.get("done") or 0, status.get("days") or 0
    if state == "running":
        pid = status.get("pid")
        if isinstance(pid, int):
            gone = not control._alive(pid)
        else:                                    # folders written before the engine recorded its pid
            age = _age_s(_parse_ts(status.get("updated_at")), now)
            gone = age is None or age > STALE_AFTER_S
        if gone:
            return "stale", f"stopped at day {done} of {days}"
        return "running", f"day {done} of {days}"
    if state == "failed":
        return "failed", (status.get("error") or "failed")[:200]
    if state == "queued":
        nb = _parse_ts(status.get("not_before"))
        return "queued", f"starts after {nb:%H:%M}" if nb and (_age_s(nb, now) or 0) < 0 else "waiting to start"
    if state == "done":
        return "done", f"{days} day{'s' if days != 1 else ''}"
    return state, state


def _row(path: Path, status: dict[str, Any], now: datetime) -> dict[str, Any]:
    summary = _read_json(path / "summary.json") or {}
    request = _read_json(path / "request.json") or {}
    state, label = state_of(status, now)
    totals = summary.get("totals") or {}
    return dict(
        name=path.name, algo_id=status.get("algo_id"), version=status.get("version"),
        start=status.get("start"), end=status.get("end"), days=status.get("days"), done=status.get("done"),
        option_prices=status.get("option_prices") or "model", state=state, label=label,
        started_at=status.get("started_at"), updated_at=status.get("updated_at"), elapsed_s=status.get("elapsed_s"),
        overrides=status.get("overrides") or {}, sweep=request.get("sweep"),
        totals={k: totals.get(k) for k in TOTALS} if totals else None,
        missing_options=len((summary.get("coverage") or {}).get("missing_options") or []) if summary else None,
    )


def list_runs(platform: PlatformConfig, now: datetime) -> list[dict[str, Any]]:
    """One row per backtest folder, newest first. Folders without status.json (studies) are skipped."""
    base = root(platform)
    if not base.is_dir():
        return []
    rows = []
    for path in base.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        status = _read_json(path / "status.json")
        if isinstance(status, dict):
            rows.append(_row(path, status, now))
    rows.sort(key=lambda r: r["started_at"] or "", reverse=True)
    return rows


# ---------------------------------------------------------------- the algo's config, for the form

KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][\w-]*)\s*:(.*)$")


def _trailing_comment(rest: str) -> str | None:
    """The ``# comment`` after a value, ignoring a # inside quotes."""
    quote = None
    for i, ch in enumerate(rest):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or rest[i - 1].isspace()):
            return rest[i + 1:].strip() or None
    return None


def yaml_help(path: Path) -> tuple[dict[str, str], list[str]]:
    """({dotted key: help text}, keys in file order) from config.yaml: the comment lines just above a
    key plus the comment after its value. A blank line ends a comment block."""
    help_, order, stack, pending = {}, [], [], []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, []
    for raw in lines:
        s = raw.strip()
        if not s:
            pending = []
            continue
        if s.startswith("#"):
            pending.append(s.lstrip("#").strip())
            continue
        mt = KEY_LINE.match(raw)
        if not mt:
            pending = []
            continue
        indent, key, rest = len(mt.group(1)), mt.group(2), mt.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([k for _, k in stack] + [key])
        order.append(dotted)
        text = " ".join(pending + [t for t in [_trailing_comment(rest)] if t])
        if text:
            help_[dotted] = text
        pending = []
        if not rest.split("#", 1)[0].strip():          # "key:" alone opens a nested block
            stack.append((indent, key))
    return help_, order


def algo_config(algo_id: str) -> dict[str, Any]:
    """Everything the New backtest page needs to build its form: today's values, the schema, the YAML
    comments as help text, and what backtests always change for this algo."""
    from trading_core.backtest.engine import backtest_overrides

    cls, cfg = registry.load(algo_id)
    help_, order = yaml_help(registry.algos_dir() / algo_id / "config.yaml")
    return dict(algo_id=algo_id, name=cfg.name, version=cfg.version, values=cfg.model_dump(mode="json"),
                schema=cls.config_model.model_json_schema(), help=help_, order=order,
                backtest_overrides=backtest_overrides(algo_id),
                trade_window_days=getattr(cfg, "trade_window_days", None))


# ---------------------------------------------------------------- queueing a run

FIXED_KEYS = ("algo_id", "mode", "name", "version")      # a backtest never changes these


def _write_json(path: Path, body: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def submit(platform: PlatformConfig, req: dict[str, Any], now: datetime, today: date,
           dry_run: bool = False) -> dict[str, Any]:
    """Check a New backtest request and queue it: the folder is created now with ``state: queued``.
    ``dry_run`` only checks (a sweep checks every combination before queueing any)."""
    from trading_core.backtest.engine import backtest_overrides

    algo_id, name = req.get("algo_id"), req.get("name") or ""
    if algo_id not in registry.available():
        raise BacktestError(f"unknown strategy {algo_id!r}")
    if not NAME_RE.match(name):
        raise BacktestError("name: letters, digits, _ - . only, starting with a letter or digit, at most 80")
    if (root(platform) / name).exists():
        raise BacktestError(f"a backtest called {name!r} already exists: pick another name")
    try:
        start, end = date.fromisoformat(str(req.get("start"))), date.fromisoformat(str(req.get("end")))
    except ValueError:
        raise BacktestError("start and end must be dates (YYYY-MM-DD)") from None
    if start > end:
        raise BacktestError("start is after end")
    if end > today:
        raise BacktestError("end is in the future")
    prices = req.get("option_prices") or "real"
    if prices not in ("real", "model"):
        raise BacktestError("option_prices must be real or model")
    overrides = req.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise BacktestError("overrides must be an object")
    if fixed := [k for k in FIXED_KEYS if k in overrides]:
        raise BacktestError(f"a backtest can't change {', '.join(fixed)}")
    try:                                                   # the same load the engine does, so it fails here
        _, cfg = registry.load(algo_id, deep_merge(backtest_overrides(algo_id), int_keys(overrides)))
    except Exception as e:  # noqa: BLE001 - pydantic names the bad field
        raise BacktestError(f"invalid settings: {e}") from None
    not_before = req.get("not_before")
    if not_before and _parse_ts(not_before) is None:
        raise BacktestError("not_before must be a date and time")
    if dry_run:
        return dict(name=name, ok=True, version=cfg.version)

    folder = root(platform) / name
    folder.mkdir(parents=True)
    stamp = now.isoformat(timespec="seconds")
    _write_json(folder / "request.json", {**req, "option_prices": prices, "queued_at": stamp})
    _write_json(folder / "overrides.json", overrides)
    status = dict(name=name, algo_id=algo_id, version=cfg.version, start=str(start), end=str(end),
                  days=req.get("days"), done=0, option_prices=prices, overrides=overrides,
                  backtest_overrides=backtest_overrides(algo_id), started_at=stamp, queued_at=stamp,
                  not_before=not_before, state="queued", updated_at=stamp)
    _write_json(folder / "status.json", status)
    return _row(folder, status, now)


def spawn_backtest(platform: PlatformConfig, folder: Path, req: dict[str, Any]) -> subprocess.Popen:
    """``algo backtest`` as a background process: no window, below-normal priority, output in run.log."""
    cmd = [sys.executable, "-m", "trading_core", "backtest", "--algo", req["algo_id"], "--from", req["start"],
           "--to", req["end"], "--name", folder.name, "--options", req.get("option_prices") or "real",
           "--overrides-file", str(folder / "overrides.json")]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    out = open(folder / "run.log", "a", encoding="utf-8")          # noqa: SIM115 - the child owns it
    out.write(f"$ algo {' '.join(cmd[3:])}\n")
    out.flush()
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        return subprocess.Popen(cmd, cwd=platform.root, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    return subprocess.Popen(cmd, cwd=platform.root, env=env, stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True, preexec_fn=lambda: os.nice(5))


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


class Queue:
    """Runs queued folders one at a time. ``tick()`` does one step; ``start()`` calls it every 2 s."""

    def __init__(self, platform: PlatformConfig, spawn: Callable[..., Any] = spawn_backtest,
                 kill: Callable[[int], None] = kill_tree, poll_s: float = 2.0):
        self.platform, self.spawn, self.kill, self.poll_s = platform, spawn, kill, poll_s
        self.proc: Any = None
        self.current: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.running = False

    def start(self, clock) -> None:
        if self.running:
            return
        self.running = True

        def loop():
            try:                                             # warm the trading-days cache for the New page
                from dashboard import data_coverage
                data_coverage.days(self.platform)
            except Exception:  # noqa: BLE001
                log.exception("could not read market-data days")
            while not self._stop.is_set():
                try:
                    self.tick(clock.now())
                except Exception:  # noqa: BLE001 - the worker must survive a bad folder
                    log.exception("backtest queue")
                self._stop.wait(self.poll_s)

        threading.Thread(target=loop, name="backtest-queue", daemon=True).start()

    def _reap(self, now: datetime) -> None:
        """The process we started has ended: if it didn't record an outcome, mark it failed."""
        if self.proc is None or self.proc.poll() is None:
            return
        code, name = self.proc.returncode, self.current
        self.proc, self.current = None, None
        path = folder(self.platform, name) if name else None
        status = _read_json(path / "status.json") if path else None
        if status and status.get("state") in ("queued", "running"):
            _write_json(path / "status.json", {**status, "state": "failed", "updated_at": now.isoformat(timespec="seconds"),
                                               "error": f"the backtest process ended (exit code {code}) before finishing: see the Log tab"})

    def tick(self, now: datetime) -> str | None:
        with self._lock:
            self._reap(now)
            if self.proc is not None:
                return None
            runs = list_runs(self.platform, now)
            if any(r["state"] == "running" for r in runs):  # one at a time, terminal runs included
                return None
            waiting = []
            for r in runs:
                if r["state"] != "queued":
                    continue
                st = _read_json(root(self.platform) / r["name"] / "status.json") or {}
                nb = _parse_ts(st.get("not_before"))
                if nb is None or (_age_s(nb, now) or 0) >= 0:
                    waiting.append((st.get("queued_at") or "", r["name"]))
            if not waiting:
                return None
            name = min(waiting)[1]
            path = root(self.platform) / name
            req = _read_json(path / "request.json") or {}
            status = _read_json(path / "status.json") or {}
            self.proc = self.spawn(self.platform, path, req)
            self.current = name
            _write_json(path / "status.json", {**status, "state": "running", "pid": self.proc.pid,
                                               "updated_at": now.isoformat(timespec="seconds")})
            return name

    def stop(self, name: str, now: datetime) -> dict[str, Any]:
        """Cancel a queued run, or end a running one the dashboard started. Never touches an algo."""
        path = folder(self.platform, name)
        if path is None:
            raise BacktestError(f"no backtest called {name!r}")
        with self._lock:
            status = _read_json(path / "status.json") or {}
            state, _ = state_of(status, now)
            stamp = now.isoformat(timespec="seconds")
            if state == "queued":
                _write_json(path / "status.json", {**status, "state": "failed", "updated_at": stamp,
                                                   "error": "cancelled before it started"})
                return dict(cancelled=name)
            if state != "running":
                raise BacktestError(f"{name} is not running")
            if not (path / "request.json").exists():
                raise BacktestError(f"{name} was started from the terminal: stop it there (Ctrl+C)")
            pids = {p for p in (status.get("pid"), self.proc.pid if self.proc and self.current == name else None)
                    if isinstance(p, int)}
            for pid in pids:
                self.kill(pid)
            if self.current == name:
                self.proc, self.current = None, None
            done, days = status.get("done") or 0, status.get("days") or 0
            _write_json(path / "status.json", {**_read_json(path / "status.json"), "state": "failed", "updated_at": stamp,
                                               "error": f"stopped from the dashboard at day {done} of {days}"})
            return dict(stopped=name)


# ---------------------------------------------------------------- one run

_dbs: OrderedDict[str, Database] = OrderedDict()
_db_lock = threading.Lock()
MAX_OPEN_DBS = 4


def run_db(platform: PlatformConfig, name: str) -> Database | None:
    """The run's backtest.db, kept open for the next few requests (a run page makes several)."""
    path = folder(platform, name)
    if path is None or not (path / "backtest.db").exists():     # never create an empty db for a queued run
        return None
    key = str(path)
    with _db_lock:
        db = _dbs.pop(key, None) or Database(f"sqlite:///{(path / 'backtest.db').as_posix()}")
        _dbs[key] = db
        while len(_dbs) > MAX_OPEN_DBS:
            _dbs.popitem(last=False)[1].engine.dispose()
        return db


def _close_db(path: Path) -> None:
    with _db_lock:
        db = _dbs.pop(str(path), None)
    if db is not None:
        db.engine.dispose()             # Windows can't move a folder while SQLite holds its file


def detail(platform: PlatformConfig, name: str, now: datetime) -> dict[str, Any] | None:
    path = folder(platform, name)
    if path is None:
        return None
    status = _read_json(path / "status.json") or {}
    row = _row(path, status, now)
    snapshot = None
    db = run_db(platform, name)
    if db is not None:
        with db.session() as s:
            first = s.scalars(select(m.AlgoRun).order_by(m.AlgoRun.run_id).limit(1)).first()
            snapshot = dict(first.config_snapshot) if first and first.config_snapshot else None
    live, live_error = None, None
    try:
        live = registry.load(row["algo_id"])[1].model_dump(mode="json") if row["algo_id"] else None
    except Exception as e:  # noqa: BLE001 - a renamed or broken algo must not hide its old results
        live_error = str(e)
    return dict(row, status=status, summary=_read_json(path / "summary.json"), request=_read_json(path / "request.json"),
                config_snapshot=snapshot, live_config=live, live_config_error=live_error,
                backtest_overrides=status.get("backtest_overrides") or {},
                has_log=(path / "run.log").exists())


def run_range(platform: PlatformConfig, name: str) -> tuple[str, date, date] | None:
    """(algo_id, start, end) from the run's status, for the strategy queries."""
    path = folder(platform, name)
    status = _read_json(path / "status.json") if path else None
    if not status or not status.get("algo_id"):
        return None
    try:
        return status["algo_id"], date.fromisoformat(status["start"]), date.fromisoformat(status["end"])
    except (KeyError, TypeError, ValueError):
        return None


def days(platform: PlatformConfig, name: str) -> dict[str, Any] | None:
    """Every day of the run, compact: result or skip reason. Days the engine could not run (no index
    or no saved option candles) come from the summary's coverage."""
    path = folder(platform, name)
    db = run_db(platform, name)
    if path is None:
        return None
    rows: dict[str, dict[str, Any]] = {}
    if db is not None:
        with db.session() as s:
            nets: dict[int, list] = defaultdict(list)
            dtes: dict[int, Any] = {}
            reasons: dict[int, list] = defaultdict(list)
            for p in s.scalars(select(m.Position)):
                params = p.params or {}
                if p.net_pnl is not None:
                    nets[p.run_id].append(p.net_pnl)
                    reasons[p.run_id].append(p.exit_reason or "?")
                dtes.setdefault(p.run_id, params.get("dte"))
            for r in s.scalars(select(m.AlgoRun).order_by(m.AlgoRun.run_id)):
                d = r.trade_date.isoformat()
                ns = nets.get(r.run_id, [])
                rows[d] = dict(date=d, weekday=WEEKDAYS[r.trade_date.weekday()], run_id=r.run_id, status=r.status,
                               trades=len(ns), net=round(sum(ns), 2) if ns else None, dte=dtes.get(r.run_id),
                               exit=", ".join(dict.fromkeys(reasons.get(r.run_id, []))) or None,
                               skip_reason=None if ns else r.skip_reason)
    coverage = (_read_json(path / "summary.json") or {}).get("coverage") or {}
    for key, why in (("missing_options", "no saved option candles for the nearest expiry"),
                     ("missing_index", "no NIFTY minute data")):
        for d in coverage.get(key) or []:
            rows.setdefault(d, dict(date=d, weekday=WEEKDAYS[date.fromisoformat(d).weekday()], run_id=None,
                                    status="NOT_RUN", trades=0, net=None, dte=None, exit=None, skip_reason=why))
    return dict(name=name, days=sorted(rows.values(), key=lambda r: r["date"], reverse=True))


def log_tail(platform: PlatformConfig, name: str, lines: int = 200) -> dict[str, Any] | None:
    path = folder(platform, name)
    if path is None:
        return None
    log = path / "run.log"
    if not log.exists():
        return dict(name=name, available=False, lines=[])
    with log.open("r", encoding="utf-8", errors="replace") as f:
        tail = f.readlines()[-lines:]
    return dict(name=name, available=True, lines=[t.rstrip("\n") for t in tail])


# ---------------------------------------------------------------- trash

def trash(platform: PlatformConfig, name: str, now: datetime) -> dict[str, Any]:
    """Move the folder to data/backtests/.trash/<name>_<time>/. Nothing is ever deleted."""
    path = folder(platform, name)
    if path is None:
        raise BacktestError(f"no backtest called {name!r}")
    state, _ = state_of(_read_json(path / "status.json") or {}, now)
    if state in ("queued", "running"):
        raise BacktestError(f"{name} is {state}: stop it before moving it to the trash")
    _close_db(path)
    dest = root(platform) / TRASH / f"{name}_{now:%Y%m%d-%H%M%S}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    with _cache_lock:
        for p in [p for p in _json_cache if path in p.parents]:
            _json_cache.pop(p, None)
    return dict(trashed=name, to=dest.relative_to(platform.data_dir).as_posix())
