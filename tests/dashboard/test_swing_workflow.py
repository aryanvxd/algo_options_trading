"""Swing section: sub-menu wiring, the weekend scan from the dashboard, picks, settings, watchlists,
journal and fundamentals."""

import json
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dashboard import swing_jobs
from dashboard.app import create_app
from swing.settings import load_user_config
from tests.dashboard.test_swing_pages import seed
from trading_core.clock import IST

W = {"X-Algo-Desk": "1"}


class FakeProc:
    pid = 4242

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


@pytest.fixture
def spawned():
    return []


@pytest.fixture
def client(platform, db, spawned):
    def spawn(platform_, args, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("$ algo " + " ".join(args) + "\n", encoding="utf-8")
        spawned.append(args)
        return FakeProc()

    jobs = swing_jobs.SwingJobs(platform, spawn=spawn)
    return TestClient(create_app(db, platform=platform, swing_scan_jobs=jobs))


def test_swing_submenu_is_wired_in(client):
    html = client.get("/").text
    for page in ("swing.js", "swing_run.js", "swing_picks.js", "swing_watch.js", "swing_journal.js", "swing_settings.js",
                 "swing_workflow.css"):
        assert f"/static/pages/{page}" in html
        assert client.get(f"/static/pages/{page}").status_code == 200
    assert 'data-submenu="swing"' in html and 'id="menu-swing"' in html and 'id="nav-watchlists"' in html
    for view in ("run", "picks", "journal", "holdings", "orders", "settings", "fundamentals"):
        assert f'data-page="swing/{view}"' in html
    groups = re.findall(r'class="nav-group[^"]*" data-group="([\w-]+)"', html)
    assert {"sw-workflow", "sw-watchlists", "sw-tracking", "sw-portfolio", "sw-setup"} <= set(groups)
    assert len(groups) == len(set(groups))
    js = client.get("/static/app.js").text
    assert "SUBMENUS = ['backtests', 'swing']" in js


def test_scan_runs_on_stored_candles_without_kite(client, spawned):
    assert client.get("/api/swing/scan").json() == {"job": None, "scan": None}
    r = client.post("/api/swing/scan", json={"refresh": False}, headers=W)
    assert r.status_code == 201 and r.json()["running"]
    assert spawned == [["swing", "scan", "--no-refresh"]]
    assert client.post("/api/swing/scan", json={"refresh": False}, headers=W).status_code == 409   # one at a time
    st = client.get("/api/swing/scan").json()
    assert st["job"]["running"] and st["job"]["lines"][0].startswith("$ algo swing scan")


def test_scan_refresh_needs_kite_and_writes_need_the_header(client, monkeypatch):
    assert client.post("/api/swing/scan", json={"refresh": False}).status_code == 403
    from dashboard import control
    monkeypatch.setattr(control, "status", lambda *a, **k: {"kite": {"logged_in": False}})
    r = client.post("/api/swing/scan", json={"refresh": True}, headers=W)
    assert r.status_code == 409 and "log in" in r.json()["detail"]


def test_market_hours_need_a_confirmation(platform):
    jobs = swing_jobs.SwingJobs(platform, spawn=lambda *a: FakeProc())
    monday_noon = datetime(2026, 11, 9, 12, 0, tzinfo=IST)
    assert swing_jobs.market_hours(monday_noon) and not swing_jobs.market_hours(datetime(2026, 11, 8, 12, 0, tzinfo=IST))
    with pytest.raises(swing_jobs.ScanError, match="market hours"):
        jobs.start(True, True, monday_noon)
    assert jobs.start(True, True, monday_noon, confirm_market_hours=True)["running"]


def test_picks_before_and_after_a_scan(client, platform):
    assert client.get("/api/swing/picks").json()["available"] is False
    folder = platform.reports_dir / "swing"
    folder.mkdir(parents=True)
    (folder / "picks.json").write_text(json.dumps({"available": True, "picks": []}), encoding="utf-8")
    assert client.get("/api/swing/picks").json()["available"] is True


def test_settings_save_validate_and_reset(client, platform):
    s = client.get("/api/swing/settings").json()
    assert s["values"]["funnel"]["top_n"] == 10 and "zones" not in s["values"] and s["overrides"] == {}
    r = client.put("/api/swing/settings", json={"overrides": {"funnel": {"top_n": 5}, "setups": {"min_rr": 2.5}}}, headers=W)
    assert r.status_code == 200 and r.json()["values"]["funnel"]["top_n"] == 5
    cfg = load_user_config(platform)
    assert cfg.funnel.top_n == 5 and cfg.setups.min_rr == 2.5 and cfg.zones.pivot_daily == 5
    assert client.put("/api/swing/settings", json={"overrides": {"funnel": {"top_n": 0}}}, headers=W).status_code == 400
    assert client.put("/api/swing/settings", json={"overrides": {"indices": []}}, headers=W).status_code == 400
    assert client.delete("/api/swing/settings", headers=W).status_code == 200
    assert load_user_config(platform).funnel.top_n == 10


def test_watchlists_api(client, platform):
    seed(platform, "POWERGRID", "swing")
    ls = client.get("/api/swing/watchlists").json()["lists"]
    assert len(ls) == 5 and ls[0]["kind"] == "auto"
    auto, mine = ls[0]["id"], ls[1]["id"]
    assert client.post(f"/api/swing/watchlists/{auto}/items", json={"symbol": "POWERGRID"}, headers=W).status_code == 400
    r = client.post(f"/api/swing/watchlists/{mine}/items", json={"symbol": "powergrid", "note": "watch 250"}, headers=W)
    assert r.status_code == 201 and r.json() == {"added": True}
    assert client.post(f"/api/swing/watchlists/{mine}/items", json={"symbol": "NOPE"}, headers=W).status_code == 400
    w = client.get(f"/api/swing/watchlists/{mine}").json()
    row = w["rows"][0]
    assert row["symbol"] == "POWERGRID" and row["note"] == "watch 250" and row["price"] and row["position"] != "no data"
    assert client.put(f"/api/swing/watchlists/{mine}", json={"name": "Power"}, headers=W).json()["name"] == "Power"
    assert client.delete(f"/api/swing/watchlists/{mine}/items/POWERGRID", headers=W).json() == {"removed": True}
    assert client.get("/api/swing/watchlists/999").status_code == 404


def test_journal_and_fundamentals_api(client, platform):
    j = client.get("/api/swing/journal").json()
    assert j["rows"] == [] and "not an edge" in j["note"]
    assert client.post("/api/swing/journal/update", headers=W).json() == {"updated": 0}
    r = client.post("/api/swing/fundamentals", headers=W,
                    json={"as_of": "2026-11-01", "rows": [{"symbol": "POWERGRID", "metrics": {"ROCE %": 12.5, "P/E": "18"}}]})
    assert r.status_code == 201 and r.json() == {"stocks": 1, "values": 2}
    f = client.get("/api/swing/fundamentals").json()
    assert f["stocks"] == 1 and {m["metric"] for m in f["metrics"]} == {"roce", "p_e"}
    assert "roce" in client.get("/api/swing/settings").json()["metrics"]
    seed(platform, "POWERGRID", "swing")
    assert client.get("/api/swing/stock/POWERGRID").json()["fundamentals"] == {"roce": 12.5, "p_e": 18.0}
    assert client.delete("/api/swing/fundamentals", headers=W).json() == {"removed": 2}


def test_missing_tables_say_upgrade(platform):
    from trading_core.db.engine import Database
    bare = Database(platform.db_url.replace("test.db", "bare.db"))      # no tables at all
    c = TestClient(create_app(bare, platform=platform))
    r = c.get("/api/swing/watchlists")
    assert r.status_code == 503 and "algo db upgrade" in r.json()["detail"]


def test_scan_command_writes_stages_picks_and_journal(platform, db):
    """`algo swing scan --no-refresh` end to end on stored candles (no Kite)."""
    import argparse

    from swing.cli import cmd_swing

    seed(platform, "POWERGRID", "swing", volume=10_000_000)
    seed(platform, "NIFTY ENERGY", "swing_indices", volume=0)
    assert cmd_swing(argparse.Namespace(action="scan", no_refresh=True), platform) == 0
    folder = platform.reports_dir / "swing"
    st = json.loads((folder / "scan_status.json").read_text(encoding="utf-8"))
    assert st["ok"] is True and [s["state"] for s in st["stages"]] == ["skipped", "done", "done", "done", "done"]
    picks = json.loads((folder / "picks.json").read_text(encoding="utf-8"))
    assert picks["available"] and picks["funnel"]["sectors"] == 1
