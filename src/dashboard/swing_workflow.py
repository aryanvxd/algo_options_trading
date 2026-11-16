"""The Swing section's workflow pages: Picks, Watchlists, Journal, Settings and Fundamentals.

Picks read picks.json, written by the weekend scan (``swing.funnel``). Watchlists, the journal and
fundamentals live in the platform database (tables from migration 0006). Settings are saved to
data/swing/overrides.json. Only reads reach Kite (a watchlist's last prices); nothing is ordered.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from swing import fundamentals, tracking, watchlists
from swing.bars import daily_bars
from swing.chart import NOTE
from swing.funnel import RANKING_NOTE
from swing.overview import place
from swing.settings import load_swing_config, load_user_config, read_overrides, save_overrides
from swing.universe import load_swing_universe
from swing.zones import find_zones
from trading_core.config import PlatformConfig
from trading_core.db.engine import Database
from trading_core.kite.client import KiteClient
from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)

# What the Settings page may change (the rest of config.yaml stays as it is: zones, indices, backtest).
EDITABLE_TOP = {"min_traded_value_cr", "overview_near_atr"}
EDITABLE_SECTIONS = {"setups", "funnel"}


def _read(platform: PlatformConfig, name: str) -> dict[str, Any] | None:
    path = platform.reports_dir / "swing" / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- picks

def picks(platform: PlatformConfig) -> dict[str, Any]:
    return _read(platform, "picks.json") or {"available": False, "note": NOTE, "ranking_note": RANKING_NOTE}


# ---------------------------------------------------------------- settings

def _editable(dump: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dump.items() if k in EDITABLE_TOP | EDITABLE_SECTIONS}


def settings(platform: PlatformConfig, db: Database) -> dict[str, Any]:
    return {"values": _editable(load_user_config(platform).model_dump()),
            "defaults": _editable(load_swing_config().model_dump()),
            "overrides": read_overrides(platform),
            "metrics": [m["metric"] for m in fundamentals.summary(db)["metrics"]]}


def save_settings(platform: PlatformConfig, overrides: dict[str, Any]) -> dict[str, Any]:
    bad = [k for k in overrides if k not in EDITABLE_TOP | EDITABLE_SECTIONS]
    if bad:
        raise ValueError(f"not editable here: {', '.join(sorted(bad))}")
    cfg = save_overrides(platform, overrides)          # raises ValueError (pydantic) on a bad value
    return {"values": _editable(cfg.model_dump()), "overrides": overrides}


# ---------------------------------------------------------------- watchlists

def _known_symbols(platform: PlatformConfig) -> set[str]:
    cfg = load_user_config(platform)
    try:
        u = load_swing_universe(platform, cfg.universe)
    except ValueError:
        return set()
    return set(u.symbols) | {ix.name for ix in u.indices}


def _levels(platform: PlatformConfig, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Zones, ATR and last close of each symbol: from the last scan, else from its stored candles."""
    ov = _read(platform, "overview.json") or {}
    rows = {r["symbol"]: r for r in ov.get("stocks", []) + ov.get("sectors", [])}
    out = {s: {"close": rows[s]["close"], "atr": rows[s].get("atr") or 0.0, "zones": rows[s].get("zones", []),
               "industry": rows[s].get("industry", "")} for s in symbols if s in rows}
    missing = [s for s in symbols if s not in out]
    if missing:
        cfg = load_user_config(platform)
        store = MarketStore(platform.marketdata.dir)
        bars = tracking.load_bars(store, cfg, missing)
        bars.update(daily_bars(store, cfg.index_universe, [s for s in missing if s not in bars]))
        for s, b in bars.items():
            rep = find_zones(b, cfg.zones)
            out[s] = {"close": float(b.close[-1]), "atr": rep.atr or 0.0,
                      "zones": [[z.lo, z.hi] for z in rep.zones], "industry": ""}
    return out


def watchlist(platform: PlatformConfig, db: Database, list_id: int, client: KiteClient | None,
              now: datetime) -> dict[str, Any]:
    head, items = watchlists.items(db, list_id)
    syms = [i["symbol"] for i in items]
    lv = _levels(platform, syms)
    near = load_user_config(platform).overview_near_atr
    prices: dict[str, float] = {}
    if client and syms:
        try:
            quotes = client.ltp([f"NSE:{s}" for s in syms])
            prices = {k.split(":", 1)[1]: float(v["last_price"]) for k, v in quotes.items() if v.get("last_price")}
        except Exception as e:  # noqa: BLE001 - the list still works from the stored closes
            log.warning("swing: watchlist prices failed: %s", e)
    rows = []
    for it in items:
        lev = lv.get(it["symbol"])
        price = prices.get(it["symbol"], lev["close"] if lev else None)
        p = place(price, lev["zones"], lev["atr"], near) if lev and price else None
        base = it["price_added"]
        rows.append({**it, "industry": lev["industry"] if lev else "", "price": price,
                     "since_pct": round((price / base - 1) * 100, 2) if price and base else None,
                     "to_entry_pct": round((price / it["entry"] - 1) * 100, 2) if price and it["entry"] else None,
                     "position": p.position if p else "no data", "zone": list(p.zone) if p and p.zone else None,
                     "support": list(p.support) if p and p.support else None, "support_atr": p.support_atr if p else None,
                     "resistance": list(p.resistance) if p and p.resistance else None,
                     "resistance_atr": p.resistance_atr if p else None})
    return {**head, "live": bool(prices), "as_of": now.isoformat(timespec="seconds"), "note": NOTE, "rows": rows}


def add_to_watchlist(platform: PlatformConfig, db: Database, list_id: int, symbol: str, note: str | None,
                     now: datetime) -> bool:
    symbol = symbol.strip().upper()
    if symbol not in _known_symbols(platform) and not _levels(platform, [symbol]):
        raise watchlists.WatchlistError(f"{symbol} is not in the swing universe and has no stored candles")
    lv = _levels(platform, [symbol]).get(symbol)
    return watchlists.add(db, list_id, symbol, now, note=note, price=lv["close"] if lv else None)


# ---------------------------------------------------------------- journal

def journal(db: Database) -> dict[str, Any]:
    out = tracking.journal(db)
    out["note"] = RANKING_NOTE
    return out


def update_journal(platform: PlatformConfig, db: Database, now: datetime) -> dict[str, Any]:
    cfg = load_user_config(platform)
    store = MarketStore(platform.marketdata.dir)
    n = tracking.update(db, lambda syms: tracking.load_bars(store, cfg, syms), cfg, now)
    return {"updated": n}


# ---------------------------------------------------------------- fundamentals

def upload_fundamentals(db: Database, rows: list[dict[str, Any]], as_of: str | None, now: datetime) -> dict[str, Any]:
    day = date.fromisoformat(as_of) if as_of else None
    return fundamentals.save(db, rows, day, now)
