"""scan_status.json: how far ``algo swing scan`` has got, for the dashboard's Weekend run page."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_core.clock import IST

STAGES = [("refresh", "Refresh candles"), ("sectors", "Sectors"), ("stocks", "Stocks"),
          ("picks", "Picks"), ("journal", "Watchlist and journal")]


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


class ScanStatus:
    def __init__(self, folder: Path, refresh: bool):
        self.path = folder / "scan_status.json"
        self.data: dict[str, Any] = {"started_at": _now(), "finished_at": None, "ok": None, "refresh": refresh,
                                     "error": None,
                                     "stages": [{"key": k, "label": label, "state": "waiting", "detail": None}
                                                for k, label in STAGES]}
        self._write()

    def _stage(self, key: str) -> dict[str, Any]:
        return next(s for s in self.data["stages"] if s["key"] == key)

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        tmp.replace(self.path)

    def _set(self, key: str, state: str, detail: str | None = None) -> None:
        s = self._stage(key)
        s["state"], s["detail"] = state, detail
        s["at"] = _now()
        self._write()

    def start(self, key: str) -> None:
        self._set(key, "running")

    def done(self, key: str, detail: str | None = None) -> None:
        self._set(key, "done", detail)

    def skip(self, key: str, detail: str | None = None) -> None:
        self._set(key, "skipped", detail)

    def fail_stage(self, key: str, detail: str) -> None:
        self._set(key, "failed", detail)

    def fail(self, error: str) -> None:
        for s in self.data["stages"]:
            if s["state"] == "running":
                s["state"], s["detail"] = "failed", error
        self.data.update(finished_at=_now(), ok=False, error=error)
        self._write()

    def finish(self) -> None:
        ok = all(s["state"] in ("done", "skipped") for s in self.data["stages"])
        self.data.update(finished_at=_now(), ok=ok)
        self._write()


def read(folder: Path) -> dict[str, Any] | None:
    path = folder / "scan_status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
