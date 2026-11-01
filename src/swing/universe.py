"""The swing universe: sector and theme indices, and the stocks in them (spec section 2).

``algo swing universe`` downloads each index's constituent CSV from niftyindices.com and writes
``config/universes/<universe>.yaml``. The file keeps ``symbols`` (a flat list, so the platform's
``load_universe`` and ``algo data constituents`` read it as any other universe), each stock's NSE
industry (its one sector for the per-sector book limit) and each index's members.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import requests
import yaml

from swing.settings import SwingConfig
from trading_core.config import PlatformConfig, read_yaml

NSE_CSV_BASE = "https://www.niftyindices.com/IndexConstituent/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
            "Accept": "text/csv,*/*", "Referer": "https://www.niftyindices.com/"}


@dataclass
class SwingIndex:
    name: str
    kind: str
    members: list[str]


@dataclass
class SwingUniverse:
    name: str
    symbols: list[str]
    industry: dict[str, str]
    indices: list[SwingIndex]
    as_of: date | None = None
    _tags: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for ix in self.indices:
            for s in ix.members:
                self._tags.setdefault(s, []).append(ix.name)

    def indices_of(self, symbol: str) -> list[str]:
        """Every index ``symbol`` belongs to (Z10 uses the best-placed one)."""
        return self._tags.get(symbol, [])

    def index(self, name: str) -> SwingIndex:
        return next(ix for ix in self.indices if ix.name == name)


def parse_constituents(text: str) -> list[tuple[str, str]]:
    """``(symbol, industry)`` rows of a niftyindices.com constituent CSV (EQ series only).
    Raises ValueError when the response isn't that CSV (the site answers 200 with an HTML page
    for an unknown file)."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows or "Symbol" not in rows[0]:
        raise ValueError("not a constituent CSV")
    return [(r["Symbol"].strip(), (r.get("Industry") or "").strip()) for r in rows
            if r.get("Symbol") and (r.get("Series") or "EQ").strip() == "EQ"]


def fetch_csv(name: str, session: requests.Session | None = None) -> str:
    r = (session or requests).get(NSE_CSV_BASE + name, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def build_universe(cfg: SwingConfig, fetch: Callable[[str], str], as_of: date,
                   progress: Callable[[str], None] = print, pause_s: float = 0.4) -> SwingUniverse:
    """Download every index's members. An index whose file fails is reported and left out, so one
    renamed file on NSE's side doesn't stop the rest."""
    indices: list[SwingIndex] = []
    industry: dict[str, str] = {}
    for ix in cfg.indices:
        try:
            rows = parse_constituents(fetch(ix.csv))
        except Exception as e:  # noqa: BLE001
            progress(f"{ix.name:<20} FAILED ({ix.csv}): {e}")
            continue
        for sym, ind in rows:
            industry.setdefault(sym, ind)
        indices.append(SwingIndex(ix.name, ix.kind, sorted(s for s, _ in rows)))
        progress(f"{ix.name:<20} {len(rows):>3} stocks")
        if pause_s:
            time.sleep(pause_s)
    return SwingUniverse(cfg.universe, sorted(industry), dict(sorted(industry.items())), indices, as_of)


def universe_path(platform: PlatformConfig, name: str) -> Path:
    return platform.config_dir / "universes" / f"{name}.yaml"


def write_universe(platform: PlatformConfig, u: SwingUniverse) -> Path:
    header = (f"# Weekend S/R swing universe: sector and theme indices and their stocks.\n"
              f"# Written by `algo swing universe` from niftyindices.com constituent files; do not edit by\n"
              f"# hand (change src/swing/config.yaml and re-run). Refresh after NSE's March and September\n"
              f"# index reviews. industry: NSE's industry, the stock's sector for the 3-per-sector limit.\n")
    data = {"name": u.name, "exchange": "NSE", "as_of": u.as_of.isoformat() if u.as_of else None,
            "symbols": u.symbols, "industry": u.industry,
            "indices": [{"name": ix.name, "kind": ix.kind, "members": ix.members} for ix in u.indices]}
    path = universe_path(platform, u.name)
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110),
                    encoding="utf-8")
    return path


def load_swing_universe(platform: PlatformConfig, name: str = "swing") -> SwingUniverse:
    path = universe_path(platform, name)
    if not path.exists():
        raise ValueError(f"{path.name} not found: run `algo swing universe` first")
    d = read_yaml(path)
    as_of = d.get("as_of")
    return SwingUniverse(d.get("name", name), list(d.get("symbols", [])), dict(d.get("industry") or {}),
                         [SwingIndex(i["name"], i["kind"], list(i["members"])) for i in d.get("indices", [])],
                         date.fromisoformat(str(as_of)) if as_of else None)
