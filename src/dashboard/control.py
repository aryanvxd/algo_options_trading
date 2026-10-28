"""Starting and stopping algos from the dashboard.

Start opens the same `algo run --algo <id>` you would type, in its own console window, so the algo
keeps running if the dashboard is closed and its log stays on screen. Stop sends the same KILL as
`algo kill` (square off, then stop). Whether an algo is running comes from the pid file the run
writes next to its lock (``data/locks/<id>.pid``); the dashboard never takes the lock itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from trading_core.config import PlatformConfig
from trading_core.db.engine import Database
from trading_core.db.repo import Journal
from trading_core.kite.auth import load_active_session
from trading_core.strategy import registry

Launcher = Callable[[list[str], Path, str], Any]


class ControlError(Exception):
    """A start/stop the dashboard refuses, with the reason shown to the user."""


# ---------------------------------------------------------------- is it running?

def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        handle = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
                return False
            # A recycled pid would belong to some other program: insist on a Python process.
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return "python" in Path(buf.value).name.lower()
            return True
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running(platform: PlatformConfig, algo_id: str) -> dict[str, Any] | None:
    """{pid, started_at} of the live run holding this algo's lock, or None."""
    path = platform.data_dir / "locks" / f"{algo_id}.pid"
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info.get("pid"), int) and _alive(info["pid"]) else None


# ---------------------------------------------------------------- status

def status(platform: PlatformConfig, db: Database, now: datetime) -> dict[str, Any]:
    session = load_active_session(db, now)
    journal = Journal(db)
    algos = {}
    for algo_id in registry.available():
        try:
            _, cfg = registry.load(algo_id)
        except Exception:  # noqa: BLE001
            continue
        proc = running(platform, algo_id)
        run = journal.find_run(algo_id, now.date(), cfg.mode, "live")
        finished = run is not None and run.status in ("DONE", "SKIPPED")
        sched = cfg.schedule
        late = now.time() > sched.entry_deadline
        reason = None
        if proc:
            reason = "running"
        elif finished:
            reason = f"already finished today (run #{run.run_id}, {run.status.lower()})"
        elif now.time() >= sched.square_off:
            reason = f"past today's square-off ({sched.square_off:%H:%M})"
        elif session is None:
            reason = "log in to Kite first"
        algos[algo_id] = dict(
            running=proc is not None, pid=proc["pid"] if proc else None,
            started_at=proc.get("started_at") if proc else None,
            today_run=dict(run_id=run.run_id, status=run.status) if run else None,
            can_start=reason is None, start_blocked=reason if not proc else None,
            resume=run is not None and not finished,
            entry_closed=late, entry=f"{sched.entry:%H:%M}", entry_deadline=f"{sched.entry_deadline:%H:%M}",
            square_off=f"{sched.square_off:%H:%M}")
    return dict(now=now.isoformat(), kite=dict(logged_in=session is not None,
                                               valid_until=session.valid_until.isoformat() if session else None,
                                               user_id=session.user_id if session else None),
                algos=algos)


# ---------------------------------------------------------------- start / stop / login

def _python() -> str:
    return sys.executable


def launch_console(args: list[str], cwd: Path, title: str) -> subprocess.Popen:
    """Run ``python -m trading_core <args>`` in a new console window that stays open afterwards."""
    cmd = [_python(), "-m", "trading_core", *args]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":
        return subprocess.Popen(["cmd.exe", "/k", "title", title, "&&", *cmd], cwd=cwd, env=env,
                                creationflags=subprocess.CREATE_NEW_CONSOLE)
    log_dir = cwd / "logs"
    log_dir.mkdir(exist_ok=True)
    out = open(log_dir / f"{title}.console.log", "a", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def start(platform: PlatformConfig, db: Database, algo_id: str, now: datetime,
          launcher: Launcher = launch_console) -> dict[str, Any]:
    if algo_id not in registry.available():
        raise ControlError(f"unknown strategy {algo_id!r}")
    st = status(platform, db, now)["algos"][algo_id]
    if st["running"]:
        raise ControlError(f"{algo_id} is already running (pid {st['pid']})")
    if not st["can_start"]:
        raise ControlError(f"can't start {algo_id}: {st['start_blocked']}")
    launcher(["run", "--algo", algo_id], platform.root, f"AlgoDesk-{algo_id}")
    return dict(started=algo_id, resume=st["resume"], entry_closed=st["entry_closed"])


def stop(platform: PlatformConfig, db: Database, algo_id: str, now: datetime) -> dict[str, Any]:
    proc = running(platform, algo_id)
    if proc is None:
        raise ControlError(f"{algo_id} is not running")
    Journal(db).issue_command(algo_id, "KILL", now, issued_by="dashboard")
    return dict(stopping=algo_id, pid=proc["pid"])


def login(platform: PlatformConfig, launcher: Launcher = launch_console) -> dict[str, Any]:
    """Open `algo login` in its own window: it opens the Kite page in your browser, where you sign in."""
    launcher(["login"], platform.root, "AlgoDesk-login")
    return dict(started="login")
