"""The weekend scan, started from the dashboard (Swing › Weekend run).

Runs ``algo swing scan`` as a background process (no window, output in logs/swing-scan-<time>.log),
one at a time, the same way Market data runs its downloads. The scan reports each stage to
data/reports/swing/scan_status.json, which the page polls with the log's last lines.

The scan only reads from Kite (candles, holdings, GTTs and alerts). It shares the API key with the
algos, so during market hours the page asks before starting it.
"""

from __future__ import annotations

import threading
from datetime import datetime, time
from typing import Any, Callable

from dashboard.data_coverage import spawn_download
from swing import progress
from trading_core.config import PlatformConfig

MARKET_OPEN, MARKET_CLOSE = time(9, 0), time(15, 30)


class ScanError(RuntimeError):
    pass


def market_hours(now: datetime) -> bool:
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


class SwingJobs:
    def __init__(self, platform: PlatformConfig, spawn: Callable[..., Any] = spawn_download):
        self.platform, self.spawn = platform, spawn
        self.proc: Any = None
        self.job: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @property
    def folder(self):
        return self.platform.reports_dir / "swing"

    def status(self, lines: int = 80) -> dict[str, Any]:
        with self._lock:
            job = None
            if self.job is not None:
                code = self.proc.poll() if self.proc is not None else self.job.get("exit_code")
                self.job["exit_code"] = code
                job = dict(self.job, running=code is None)
        if job:
            try:
                with open(job["log"], encoding="utf-8", errors="replace") as f:
                    job["lines"] = [x.rstrip("\n") for x in f.readlines()[-lines:]]
            except OSError:
                job["lines"] = []
        return {"job": job, "scan": progress.read(self.folder)}

    def start(self, refresh: bool, kite_ok: bool, now: datetime, confirm_market_hours: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                raise ScanError("a scan is already running: wait for it to finish")
            if refresh and not kite_ok:
                raise ScanError("log in to Kite first, or run on the stored candles (untick Refresh)")
            if refresh and market_hours(now) and not confirm_market_hours:
                raise ScanError("market hours: the scan shares the Kite API key with the algos. Confirm to run anyway")
            args = ["swing", "scan"] + ([] if refresh else ["--no-refresh"])
            log = self.platform.logs_dir / f"swing-scan-{now:%Y%m%d-%H%M%S}.log"
            self.proc = self.spawn(self.platform, args, log)
            self.job = dict(args=args, refresh=refresh, started_at=now.isoformat(timespec="seconds"), log=str(log),
                            pid=self.proc.pid, exit_code=None)
            return dict(self.job, running=True)
