from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str, logs_dir: Path, algo_id: str | None = None,
                  trade_date: date | None = None, suffix: str = "") -> Path | None:
    """Console logging always; plus logs/<algo>/<date><suffix>.log when an algo is given."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)

    # Windows consoles default to cp1252, which cannot print ₹.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(FORMAT, "%H:%M:%S"))
    root.addHandler(console)

    # kiteconnect / urllib3 are chatty at INFO
    for noisy in ("urllib3", "kiteconnect", "autobahn", "twisted"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if algo_id is None:
        return None
    log_dir = logs_dir / algo_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{(trade_date or date.today()).isoformat()}{suffix}.log"
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(fh)
    return path
