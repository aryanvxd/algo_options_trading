"""Index stock lists, and the read-only GTT/ATO/alert views. The sample rows follow Kite's docs."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dashboard import swing_queries as sq
from dashboard.app import create_app
from trading_core.config import KiteConfig
from trading_core.kite.client import KiteClient

NOW = datetime(2026, 11, 7, 11, 0)

GTT_OCO = {"id": 123, "type": "two-leg", "status": "active", "created_at": "2026-11-01 10:00:00",
           "expires_at": "2027-11-01 10:00:00",
           "condition": {"exchange": "NSE", "tradingsymbol": "AAA", "trigger_values": [90.0, 120.0], "last_price": 100},
           "orders": [{"transaction_type": "SELL", "quantity": 5, "price": 89.0, "order_type": "LIMIT", "product": "CNC"},
                      {"transaction_type": "SELL", "quantity": 5, "price": 120.0, "order_type": "LIMIT", "product": "CNC"}]}
GTT_OLD = {**GTT_OCO, "id": 124, "type": "single", "status": "triggered",
           "condition": {"exchange": "NSE", "tradingsymbol": "BBB", "trigger_values": [50.0]}}
ATO = {"type": "ato", "uuid": "u1", "name": "buy dip", "status": "enabled", "lhs_exchange": "NSE", "lhs_tradingsymbol": "AAA",
       "lhs_attribute": "LastTradedPrice", "operator": "<=", "rhs_type": "constant", "rhs_constant": 95.0,
       "created_at": "2026-11-02 09:00:00",
       "basket": {"items": [{"tradingsymbol": "AAA", "params": {"transaction_type": "BUY", "quantity": 10, "price": 95.0,
                                                                "order_type": "LIMIT", "product": "CNC",
                                                                "gtt": {"target": 10.0, "stoploss": 0}}}]}}
ALERT = {"type": "simple", "uuid": "u2", "name": "", "status": "disabled", "lhs_exchange": "INDICES",
         "lhs_tradingsymbol": "NIFTY 50", "lhs_attribute": "LastTradedPrice", "operator": ">=", "rhs_type": "instrument",
         "rhs_exchange": "INDICES", "rhs_tradingsymbol": "NIFTY BANK", "rhs_attribute": "LastTradedPrice", "rhs_constant": 0}


class FakeClient:
    def __init__(self, gtts=(), alerts=(), prices=None, broken=()):
        self._gtts, self._alerts, self.prices, self.broken = list(gtts), list(alerts), prices or {}, broken
        self.calls = []

    def gtts(self):
        self.calls.append("gtts")
        if "gtts" in self.broken:
            raise RuntimeError("gtt down")
        return self._gtts

    def alerts(self):
        self.calls.append("alerts")
        if "alerts" in self.broken:
            raise RuntimeError("alerts down")
        return self._alerts

    def ltp(self, keys):
        self.calls.append("ltp")
        if "ltp" in self.broken:
            raise RuntimeError("ltp down")
        return {k: {"last_price": self.prices[k.split(":")[1]]} for k in keys if k.split(":")[1] in self.prices}


def test_orders_normalise_all_three_kinds():
    out = sq.orders(FakeClient([GTT_OCO, GTT_OLD], [ATO, ALERT]), NOW)
    assert out["available"] and out["errors"] == []
    by_id = {r["id"]: r for r in out["rows"]}
    g = by_id["123"]
    assert g["kind"] == "gtt" and g["symbol"] == "AAA" and g["active"] and g["condition"] == "90 / 120 (OCO)"
    assert g["levels"] == [{"price": 90.0, "label": "GTT SELL"}, {"price": 120.0, "label": "GTT SELL"}]
    assert g["orders"][0] == "SELL 5 at 89 LIMIT CNC" and g["ends"].startswith("2027")
    assert by_id["124"]["active"] is False
    a = by_id["u1"]
    assert a["kind"] == "ato" and a["active"] and a["condition"] == "LastTradedPrice <= 95"
    assert a["levels"] == [{"price": 95.0, "label": "ATO"}]
    assert a["orders"] == ["BUY 10 at 95 LIMIT CNC, then GTT target 10"]
    s = by_id["u2"]
    assert s["kind"] == "alert" and not s["active"] and s["levels"] == []        # compares two instruments: no line
    assert "NIFTY BANK" in s["condition"]
    assert [r["active"] for r in out["rows"]] == [True, True, False, False]      # active first


def test_orders_without_a_login_or_with_one_list_down():
    assert sq.orders(None, NOW) == {"available": False, "reason": sq.NOT_LOGGED_IN, "rows": [], "errors": []}
    out = sq.orders(FakeClient([GTT_OCO], [ATO], broken=("alerts",)), NOW)
    assert [r["kind"] for r in out["rows"]] == ["gtt"] and "alerts" in out["errors"][0]


def test_only_reads_reach_kite():
    """The queries may call these read methods and nothing else on the client."""
    client = FakeClient([GTT_OCO], [ATO])
    sq.orders(client, NOW)
    assert client.calls == ["gtts", "alerts"]


def overview_file(platform):
    stock = {"symbol": "AAA", "industry": "Power", "indices": ["NIFTY ENERGY"], "close": 100.0, "week_pct": 1.0, "atr": 2.0,
             "zones": [[90.0, 95.0], [99.0, 101.0], [110.0, 115.0]]}
    other = {**stock, "symbol": "BBB", "indices": ["NIFTY BANK"]}
    data = {"available": True, "near_atr": 1.0, "candles_to": "2026-11-06", "sectors": [{"symbol": "NIFTY ENERGY"}, {"symbol": "NIFTY BANK"}],
            "stocks": [stock, other]}
    folder = platform.reports_dir / "swing"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "overview.json").write_text(json.dumps(data), encoding="utf-8")


def test_index_stocks_at_the_close_without_a_login(platform):
    overview_file(platform)
    out = sq.index_stocks(platform, "NIFTY ENERGY", None, NOW)
    assert out["live"] is False and [r["symbol"] for r in out["rows"]] == ["AAA"]
    r = out["rows"][0]
    assert r["price"] == 100.0 and r["position"] == "in a zone" and r["zone"] == [99.0, 101.0]
    assert sq.index_stocks(platform, "NIFTY IT", None, NOW) is None


@pytest.mark.parametrize("price,position,support_atr", [(96.0, "near support", 0.5), (108.5, "near resistance", 3.75), (104.0, "mid-range", 1.5)])
def test_index_stocks_move_with_the_live_price(platform, price, position, support_atr):
    overview_file(platform)
    out = sq.index_stocks(platform, "NIFTY ENERGY", FakeClient(prices={"AAA": price}), NOW)
    r = out["rows"][0]
    assert out["live"] is True and r["price"] == price and r["position"] == position and r["support_atr"] == support_atr
    assert r["change_pct"] == pytest.approx((price / 100 - 1) * 100, abs=0.01)


def test_index_stocks_survive_a_failed_quote(platform):
    overview_file(platform)
    out = sq.index_stocks(platform, "NIFTY ENERGY", FakeClient(broken=("ltp",)), NOW)
    assert out["live"] is False and out["rows"][0]["price"] == 100.0


def test_api_routes(platform, db, monkeypatch):
    overview_file(platform)
    client = TestClient(create_app(db, platform=platform))
    assert client.get("/api/swing/orders").json()["available"] is False            # no session in a fresh DB
    assert client.get("/api/swing/index/NIFTY%20ENERGY/stocks").json()["live"] is False
    assert client.get("/api/swing/index/NOPE/stocks").status_code == 404
    monkeypatch.setattr(sq, "kite_client", lambda *a: FakeClient([GTT_OCO], [ATO], {"AAA": 96.0}))
    assert len(client.get("/api/swing/orders").json()["rows"]) == 2
    assert client.get("/api/swing/index/NIFTY%20ENERGY/stocks").json()["rows"][0]["position"] == "near support"


# The only /api/swing routes that write, and what they write: local state, never Kite.
LOCAL_WRITES = {
    ("POST", "/api/swing/scan"),                                   # starts `algo swing scan` (reads Kite)
    ("PUT", "/api/swing/settings"), ("DELETE", "/api/swing/settings"),            # data/swing/overrides.json
    ("PUT", "/api/swing/watchlists/{list_id}"),                                    # watchlist tables
    ("POST", "/api/swing/watchlists/{list_id}/items"), ("DELETE", "/api/swing/watchlists/{list_id}/items/{symbol}"),
    ("POST", "/api/swing/journal/update"),                                         # swing_picks table
    ("POST", "/api/swing/fundamentals"), ("DELETE", "/api/swing/fundamentals"),   # fundamentals table
}
KITE_WRITES = ("place_order", "modify_order", "cancel_order", "exit_order", "place_gtt", "modify_gtt", "delete_gtt",
               "create_alert", "modify_alert", "delete_alert", "convert_position")


def test_no_route_writes_to_kite(platform, db):
    """The swing API writes only local state: every non-GET /api/swing route is one of LOCAL_WRITES,
    and no swing code calls a Kite method that changes anything there."""
    app = create_app(db, platform=platform)
    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/swing")]
    writes = {(m, r.path) for r in routes for m in r.methods if m != "GET"}
    assert len(routes) >= 5 and writes == LOCAL_WRITES
    from pathlib import Path

    import dashboard
    import swing
    sources = list(Path(swing.__file__).parent.glob("*.py")) + list(Path(dashboard.__file__).parent.glob("swing_*.py"))
    for f in sources:
        text = f.read_text(encoding="utf-8")
        assert not [w for w in KITE_WRITES if w in text], f.name


class FakeKite:
    _routes = {"gtt": "/gtt/triggers"}

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def _get(self, route, params=None):
        assert route == "alerts" and self._routes["alerts"] == "/alerts"
        self.asked.append(params["page"])
        return self.pages[min(params["page"], len(self.pages)) - 1]


def client_with(pages):
    c = KiteClient.__new__(KiteClient)
    import threading
    c.cfg, c.kite, c._lock, c._last_call = KiteConfig(), FakeKite(pages), threading.Lock(), 0.0
    return c


def test_alerts_pages_and_drops_deleted():
    full = [{"uuid": str(i), "status": "enabled"} for i in range(100)]
    c = client_with([full, [{"uuid": "x", "status": "deleted"}, {"uuid": "y", "status": "disabled"}]])
    assert {r["uuid"] for r in c.alerts()} == {str(i) for i in range(100)} | {"y"}
    assert c.kite.asked == [1, 2]


def test_alerts_stop_if_paging_is_ignored():
    full = [{"uuid": str(i), "status": "enabled"} for i in range(100)]
    c = client_with([full])                       # the same page every time
    assert len(c.alerts()) == 100 and c.kite.asked == [1, 2]
