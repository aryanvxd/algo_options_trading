import json
from datetime import date, timedelta

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from tests.swing.test_zones import saw
from trading_core.marketdata.store import MarketStore


def seed(platform, symbol, universe, volume=1000):
    rows, d = [], date(2025, 1, 6)
    for lo, hi, close in saw(400):
        while d.weekday() > 4:
            d += timedelta(days=1)
        rows.append(dict(trade_date=d, symbol=symbol, open=close, high=hi, low=lo, close=close, volume=volume, source="kite"))
        d += timedelta(days=1)
    store = MarketStore(platform.marketdata.dir)
    for year in sorted({r["trade_date"].year for r in rows}):
        store.write_year("equity_daily", universe, year, "kite",
                         pa.Table.from_pylist([r for r in rows if r["trade_date"].year == year]),
                         ("trade_date", "symbol"))


@pytest.fixture
def client(platform, db):
    return TestClient(create_app(db, platform=platform))


def test_overview_before_and_after_a_scan(client, platform):
    assert client.get("/api/swing/overview").json()["available"] is False
    folder = platform.reports_dir / "swing"
    folder.mkdir(parents=True)
    (folder / "overview.json").write_text(json.dumps({"available": True, "sectors": [], "stocks": []}), encoding="utf-8")
    assert client.get("/api/swing/overview").json()["available"] is True


def test_stock_page_data(client, platform):
    seed(platform, "POWERGRID", "swing")
    s = client.get("/api/swing/stock/POWERGRID").json()
    assert s["symbol"] == "POWERGRID" and s["is_index"] is False and s["industry"] == "Power"
    assert "NIFTY ENERGY" in s["indices"] and "held no more often" in s["note"]
    assert len(s["daily"]) > len(s["weekly"]) > 0 and s["daily"][0]["time"] < s["daily"][-1]["time"]
    assert s["zones"] and s["profile"] and s["zones"][0]["hi"] > s["zones"][-1]["hi"]
    weeks = {c["time"] for c in s["weekly"]}
    assert all(p["week"] in weeks for z in s["zones"] for p in z["pivots"])     # weekly markers land on a bar
    assert "score" not in s["zones"][0]                                         # distances, not scores


def test_index_has_no_volume_profile(client, platform):
    seed(platform, "NIFTY ENERGY", "swing_indices", volume=0)
    s = client.get("/api/swing/stock/NIFTY%20ENERGY").json()
    assert s["is_index"] is True and s["profile"] == [] and s["zones"]


def test_unknown_or_missing_symbol_is_404(client):
    assert client.get("/api/swing/stock/NOTASTOCK").status_code == 404
    assert client.get("/api/swing/stock/POWERGRID").status_code == 404          # in the universe, no candles


def test_page_is_wired_in(client):
    html = client.get("/").text
    assert "/static/pages/swing.js" in html and 'data-page="swing"' in html
    assert "/static/vendor/lightweight-charts/" in html
    assert client.get("/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js").status_code == 200
    assert client.get("/static/vendor/lightweight-charts/LICENSE").status_code == 200
