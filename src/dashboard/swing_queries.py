"""Read-side queries for the Swing pages (spec weekend-sr-swing.md, section 8).

The Sectors page reads the overview.json that ``algo swing scan`` wrote, the Holdings page the
holdings.json that ``algo swing holdings`` wrote; the Stock page runs the zone engine on one symbol's
stored candles when it is opened.

Only reads reach Kite: an index page's last prices, and the GTTs, ATOs and alerts. Nothing here
creates, changes or deletes anything there, and the orders aren't saved to disk. Without a valid
login these fall back quietly.
"""

from __future__ import annotations

import bisect
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from swing.bars import Bars, avg_traded_value_cr, daily_bars, weekly
from swing.chart import NOTE
from swing.holdings import load as load_holdings
from swing.overview import place, row_for
from swing.settings import load_swing_config
from swing.universe import load_swing_universe
from swing.zones import Zone, find_zones, volume_profile
from trading_core.clock import Clock
from trading_core.config import PlatformConfig, load_secrets
from trading_core.db.engine import Database
from trading_core.kite.auth import NotLoggedIn
from trading_core.kite.client import KiteClient
from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)
PROFILE_BIN = 5                          # volume-profile bins of 0.1 ATR per bar drawn: 0.5 ATR
NOT_LOGGED_IN = "Not logged in to Kite today. Run `algo login`."


def kite_client(db: Database, platform: PlatformConfig, clock: Clock) -> KiteClient | None:
    """Today's Kite client, or None without a valid login (or keys)."""
    try:
        return KiteClient.from_db(db, platform.kite, load_secrets(platform.root), clock)
    except (NotLoggedIn, RuntimeError):
        return None


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


def holdings(platform: PlatformConfig) -> dict[str, Any]:
    return load_holdings(platform)


def index_stocks(platform: PlatformConfig, name: str, client: KiteClient | None, now: datetime) -> dict[str, Any] | None:
    """An index's liquid stocks from the last scan, placed against their zones at the last price
    (live when logged in, the scan's close otherwise). None if the scan doesn't have that index."""
    ov = overview(platform)
    if not ov.get("available") or not any(s["symbol"] == name for s in ov["sectors"]):
        return None
    members = [r for r in ov["stocks"] if name in r["indices"]]
    prices: dict[str, float] = {}
    if client and members:
        try:
            quotes = client.ltp([f"NSE:{r['symbol']}" for r in members])
            prices = {k.split(":", 1)[1]: float(v["last_price"]) for k, v in quotes.items() if v.get("last_price")}
        except Exception as e:  # noqa: BLE001 - the list still works from the scan's closes
            log.warning("swing: live prices failed: %s", e)
    rows = []
    for r in members:
        price = prices.get(r["symbol"], r["close"])
        p = place(price, r.get("zones", []), r.get("atr") or 0.0, ov["near_atr"])
        rows.append({"symbol": r["symbol"], "industry": r["industry"], "price": price, "close": r["close"],
                     "change_pct": round((price / r["close"] - 1) * 100, 2), "week_pct": r["week_pct"],
                     "position": p.position, "zone": list(p.zone) if p.zone else None,
                     "support": list(p.support) if p.support else None, "support_atr": p.support_atr,
                     "resistance": list(p.resistance) if p.resistance else None, "resistance_atr": p.resistance_atr})
    return {"index": name, "live": bool(prices), "as_of": now.isoformat(timespec="seconds"),
            "candles_to": ov["candles_to"], "near_atr": ov["near_atr"], "rows": rows}


def _order_text(o: dict[str, Any]) -> str:
    price = o.get("price")
    at = f" at {price:g}" if price else ""
    return f"{o.get('transaction_type', '')} {o.get('quantity', '')}{at} {o.get('order_type', '')} {o.get('product', '')}".strip()


def _gtt_row(g: dict[str, Any]) -> dict[str, Any]:
    c = g.get("condition") or {}
    orders = g.get("orders") or []
    triggers = c.get("trigger_values") or []
    levels = [{"price": float(v), "label": f"GTT {(orders[i] if i < len(orders) else {}).get('transaction_type', '')}".strip()}
              for i, v in enumerate(triggers)]
    return {"kind": "gtt", "id": str(g.get("id")), "symbol": c.get("tradingsymbol", ""), "exchange": c.get("exchange", ""),
            "status": g.get("status", ""), "active": g.get("status") == "active", "name": "",
            "condition": " / ".join(f"{float(v):g}" for v in triggers) + (" (OCO)" if g.get("type") == "two-leg" else ""),
            "orders": [_order_text(o) for o in orders], "levels": levels,
            "created": g.get("created_at"), "ends": g.get("expires_at")}


def _alert_row(a: dict[str, Any]) -> dict[str, Any]:
    kind = "ato" if a.get("type") == "ato" else "alert"
    constant = a.get("rhs_type", "constant") == "constant"
    rhs = f"{float(a.get('rhs_constant') or 0):g}" if constant else f"{a.get('rhs_tradingsymbol', '')} {a.get('rhs_attribute', '')}".strip()
    orders = []
    for item in (a.get("basket") or {}).get("items") or []:
        params = item.get("params") or {}
        text = _order_text(params)
        gtt = {k: v for k, v in (params.get("gtt") or {}).items() if v}
        orders.append(text + (f", then GTT {', '.join(f'{k} {v:g}' for k, v in gtt.items())}" if gtt else ""))
    levels = [{"price": float(a["rhs_constant"]), "label": kind.upper() if kind == "ato" else "Alert"}] if constant and a.get("rhs_constant") else []
    return {"kind": kind, "id": str(a.get("uuid")), "symbol": a.get("lhs_tradingsymbol", ""), "exchange": a.get("lhs_exchange", ""),
            "status": a.get("status", ""), "active": a.get("status") == "enabled", "name": a.get("name", ""),
            "condition": f"{a.get('lhs_attribute', '')} {a.get('operator', '')} {rhs}".strip(),
            "orders": orders, "levels": levels, "created": a.get("created_at"), "ends": None}


def orders(client: KiteClient | None, now: datetime) -> dict[str, Any]:
    """The account's GTTs, ATOs and alerts, read from Kite now. Read-only, and never stored."""
    if client is None:
        return {"available": False, "reason": NOT_LOGGED_IN, "rows": [], "errors": []}
    rows: list[dict[str, Any]] = []
    errors = []
    for label, read, to_row in (("GTTs", client.gtts, _gtt_row), ("alerts", client.alerts, _alert_row)):
        try:
            rows += [to_row(r) for r in read()]
        except Exception as e:  # noqa: BLE001 - one list failing must not hide the other
            log.warning("swing: reading %s failed: %s", label, e)
            errors.append(f"Could not read {label}: {e}")
    rows.sort(key=lambda r: (not r["active"], r["symbol"], r["kind"]))
    return {"available": True, "as_of": now.isoformat(timespec="seconds"), "rows": rows, "errors": errors}


def stock(platform: PlatformConfig, symbol: str) -> dict[str, Any] | None:
    """Candles, zones and volume at price of a symbol with stored candles: a stock or index in the
    universe, a holding, or a stock with a GTT, ATO or alert. None if there are none."""
    cfg = load_swing_config()
    try:
        u = load_swing_universe(platform, cfg.universe)
    except ValueError:
        return None
    held = next((r for r in load_holdings(platform)["rows"] if r["symbol"] == symbol), None)
    is_index = any(ix.name == symbol for ix in u.indices)
    store = MarketStore(platform.marketdata.dir)
    key = cfg.index_universe if is_index else cfg.universe
    bars = daily_bars(store, key, [symbol]).get(symbol)
    if bars is None:                                         # outside the universe
        bars = daily_bars(store, cfg.holdings_universe, [symbol]).get(symbol)
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
            "held": {"quantity": held["quantity"], "average": held["average"]} if held else None,
            "note": NOTE, "zones_note": rep.note, "daily": _candles(shown), "weekly": _candles(wk),
            "zones": [_zone(z, wk.dates) for z in sorted(rep.zones, key=lambda z: -z.hi)],
            "profile": _profile(shown, rep.atr) if rep.atr else []}
