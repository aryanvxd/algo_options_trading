"""Holdings review (spec section 8): the user's Kite holdings through the zone engine.

Read-only: nothing here sells, places or changes anything. It shows where each holding sits; it
makes no recommendation. Holdings are personal data, so the result stays in data/reports/swing/
(git-ignored) and on the local dashboard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from swing.bars import Bars
from swing.chart import NOTE
from swing.overview import row_for
from swing.universe import SwingUniverse
from swing.zones import ZoneConfig, find_zones
from trading_core.config import PlatformConfig

CASH_EXCHANGES = ("NSE", "BSE")


@dataclass
class Holding:
    symbol: str
    exchange: str
    quantity: int
    average: float
    last_price: float


def parse(raw: list[dict[str, Any]]) -> list[Holding]:
    """Kite's holdings. ``quantity`` is settled shares only, so T+1 and pledged shares are added.
    Rows with no shares or no average (bonus-only rows have 0) are left out of the review."""
    out = []
    for h in raw:
        qty = int(h.get("quantity") or 0) + int(h.get("t1_quantity") or 0) + int(h.get("collateral_quantity") or 0)
        if qty > 0:
            out.append(Holding(h["tradingsymbol"], h.get("exchange", "NSE"), qty, float(h.get("average_price") or 0),
                               float(h.get("last_price") or 0)))
    return sorted(out, key=lambda h: h.symbol)


def order_symbols(gtts: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> set[str]:
    """Cash-market stocks with a GTT, ATO or alert, so their charts open too."""
    out = {(g.get("condition") or {}).get("tradingsymbol") for g in gtts
           if (g.get("condition") or {}).get("exchange") in CASH_EXCHANGES}
    out |= {a.get("lhs_tradingsymbol") for a in alerts if a.get("lhs_exchange") in CASH_EXCHANGES}
    return out - {None, ""}


def review_row(h: Holding, bars: Bars | None, zcfg: ZoneConfig, near_atr: float, u: SwingUniverse) -> dict[str, Any]:
    base = {"symbol": h.symbol, "exchange": h.exchange, "quantity": h.quantity, "average": h.average,
            "industry": u.industry.get(h.symbol, ""), "in_universe": h.symbol in u.industry}
    rep = find_zones(bars, zcfg) if bars is not None else None
    close = rep.close if rep else h.last_price
    invested, value = h.quantity * h.average, h.quantity * close
    money = {"close": close, "invested": round(invested, 2), "value": round(value, 2),
             "pnl": round(value - invested, 2), "pnl_pct": round((close / h.average - 1) * 100, 2) if h.average else None}
    if rep is None or not rep.atr:
        return {**base, **money, "position": None, "note": rep.note if rep else "no NSE candles", "chart": rep is not None}
    r = row_for(bars, rep, near_atr)
    in_zone = next((z for z in rep.zones if z.lo <= h.average <= z.hi), None)
    return {**base, **money, "position": r.position, "note": "", "chart": True, "atr": rep.atr,
            "vs_average_atr": round((close - h.average) / rep.atr, 2) if h.average else None,
            "week_pct": r.week_pct, "month_pct": r.month_pct,
            "support": [r.support.lo, r.support.hi] if r.support else None, "support_atr": r.support_atr,
            "resistance": [r.resistance.lo, r.resistance.hi] if r.resistance else None,
            "resistance_atr": r.resistance_atr,
            "average_zone": [in_zone.lo, in_zone.hi] if in_zone else None,
            "no_support_below": bool(rep.zones) and r.support is None}


def review(holdings: list[Holding], bars: dict[str, Bars], zcfg: ZoneConfig, near_atr: float, u: SwingUniverse,
           fetched_at: datetime) -> dict[str, Any]:
    rows = [review_row(h, bars.get(h.symbol), zcfg, near_atr, u) for h in holdings]
    invested = sum(r["invested"] for r in rows)
    value = sum(r["value"] for r in rows)
    return {"available": True, "fetched_at": fetched_at.isoformat(timespec="minutes"), "note": NOTE, "rows": rows,
            "totals": {"holdings": len(rows), "invested": round(invested, 2), "value": round(value, 2),
                       "pnl": round(value - invested, 2),
                       "pnl_pct": round((value / invested - 1) * 100, 2) if invested else None}}


def path(platform: PlatformConfig) -> Path:
    return platform.reports_dir / "swing" / "holdings.json"


def load(platform: PlatformConfig) -> dict[str, Any]:
    p = path(platform)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"available": False, "note": NOTE, "rows": []}


def write(platform: PlatformConfig, data: dict[str, Any]) -> Path:
    p = path(platform)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p
