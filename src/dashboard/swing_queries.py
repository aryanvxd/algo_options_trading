"""Read-side queries for the Swing pages (spec weekend-sr-swing.md, section 8).

Nothing here talks to Kite. The Sectors page reads the overview.json that ``algo swing scan``
wrote; the Stock page runs the zone engine on one symbol's stored candles when it is opened.
"""

from __future__ import annotations

import bisect
import json
from datetime import timedelta
from typing import Any

from swing.bars import Bars, avg_traded_value_cr, daily_bars, weekly
from swing.chart import NOTE
from swing.overview import row_for
from swing.settings import load_swing_config
from swing.universe import load_swing_universe
from swing.zones import Zone, find_zones, volume_profile
from trading_core.config import PlatformConfig
from trading_core.marketdata.store import MarketStore

PROFILE_BIN = 5                          # volume-profile bins of 0.1 ATR per bar drawn: 0.5 ATR


def overview(platform: PlatformConfig) -> dict[str, Any]:
    path = platform.reports_dir / "swing" / "overview.json"
    if not path.exists():
        return {"available": False, "note": NOTE}
    return json.loads(path.read_text(encoding="utf-8"))


def _candles(b: Bars) -> list[dict[str, Any]]:
    return [{"time": d.isoformat(), "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
             "volume": int(v)} for d, o, h, lo, c, v in zip(b.dates, b.open, b.high, b.low, b.close, b.volume)]


def _zone(z: Zone, week_days: list) -> dict[str, Any]:
    def week_of(day):
        i = bisect.bisect_left(week_days, day)
        return week_days[i].isoformat() if i < len(week_days) else None

    return {"lo": z.lo, "hi": z.hi, "role": z.role, "flipped": z.flipped, "support_touches": z.count("support"),
            "resistance_touches": z.count("resistance"), "last_test": z.last_test.isoformat() if z.last_test else None,
            "breaks": [{"day": d.isoformat(), "way": w} for d, w in z.breaks], "confluence": z.confluence,
            "pivots": [{"time": p.day.isoformat(), "week": week_of(p.day), "kind": p.kind, "price": p.price}
                       for p in z.pivots]}


def _profile(b: Bars, atr: float) -> list[dict[str, float]]:
    prof = volume_profile(b, atr)
    if prof is None:
        return []
    edges, vol = prof
    out = []
    for i in range(0, len(vol), PROFILE_BIN):
        j = min(i + PROFILE_BIN, len(vol))
        out.append({"lo": float(edges[i]), "hi": float(edges[j]), "volume": float(vol[i:j].sum())})
    return out


def stock(platform: PlatformConfig, symbol: str) -> dict[str, Any] | None:
    """Candles, zones and volume at price of a stock or index in the universe; None if it isn't one."""
    cfg = load_swing_config()
    try:
        u = load_swing_universe(platform, cfg.universe)
    except ValueError:
        return None
    is_index = any(ix.name == symbol for ix in u.indices)
    if not is_index and symbol not in u.industry:
        return None
    key = cfg.index_universe if is_index else cfg.universe
    bars = daily_bars(MarketStore(platform.marketdata.dir), key, [symbol]).get(symbol)
    if bars is None:
        return None
    rep = find_zones(bars, cfg.zones)
    row = row_for(bars, rep, cfg.overview_near_atr) if rep.atr else None
    shown = bars.since(rep.asof - timedelta(days=cfg.zones.lookback_days))
    wk = weekly(shown)
    return {"symbol": symbol, "is_index": is_index, "industry": u.industry.get(symbol, ""),
            "kind": next((ix.kind for ix in u.indices if ix.name == symbol), None),
            "indices": [] if is_index else u.indices_of(symbol),
            "candles_to": rep.asof.isoformat(), "close": rep.close, "atr": rep.atr,
            "week_pct": row.week_pct if row else None, "month_pct": row.month_pct if row else None,
            "position": row.position if row else None, "traded_cr": None if is_index else round(avg_traded_value_cr(bars), 1),
            "note": NOTE, "zones_note": rep.note, "daily": _candles(shown), "weekly": _candles(wk),
            "zones": [_zone(z, wk.dates) for z in sorted(rep.zones, key=lambda z: -z.hi)],
            "profile": _profile(shown, rep.atr) if rep.atr else []}
