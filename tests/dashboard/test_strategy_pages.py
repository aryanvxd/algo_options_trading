from datetime import time

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from tests.algos.strangle_scalp.test_scalp_replay import DAY, burst, run, seed
from trading_core.clock import SimClock, ist

Q = {"mode": "paper", "source": "replay"}


@pytest.fixture
def client(platform, db):
    seed(db, platform, burst(15 * 60, 80, 60))
    run(platform, db)
    return TestClient(create_app(db, SimClock(ist(DAY, time(16, 0)))))


def test_meta_lists_every_strategy_with_its_config(client):
    strategies = {s["algo_id"]: s for s in client.get("/api/meta").json()["strategies"]}
    assert {"iron_condor", "strangle_scalp"} <= set(strategies)
    scalp = strategies["strangle_scalp"]
    assert scalp["max_trades"] == 3 and scalp["capital"] == 12000 and scalp["entry_deadline"] == "10:30"
    assert scalp["spec"].endswith("long-strangle-scalp.md")


def test_day_trades_and_trade_detail(client):
    day = client.get(f"/api/strategies/strangle_scalp/day?date={DAY}", params=Q).json()
    assert day["run"]["status"] == "DONE" and day["run"]["max_trades"] == 3
    [t] = day["trades"]
    assert t["exit_reason"] == "TARGET" and t["capital_source"] == "premium"
    assert t["roi_pct"] == pytest.approx(t["net"] / t["capital"] * 100, abs=1e-3)
    assert len(t["curve"]) > 10 and {l["side"] for l in t["legs"]} == {"BUY"}
    assert {c["name"] for c in day["checks"]} >= {"trade_day", "pair", "signal"}
    assert day["live"]["levels"]["max_trades"] == 3

    log = client.get("/api/strategies/strangle_scalp/trades", params={**Q, "end": str(DAY)}).json()["trades"]
    assert [r["position_id"] for r in log] == [t["position_id"]] and log[0]["dte"] == 0

    detail = client.get(f"/api/positions/{t['position_id']}").json()
    assert len(detail["fills"]) == 4 and all(f["status"] == "COMPLETE" for f in detail["fills"])
    assert any(c["name"] == "signal" for c in detail["checks"])


def test_payoff_of_a_long_strangle(client):
    day = client.get(f"/api/strategies/strangle_scalp/day?date={DAY}", params=Q).json()
    t = day["trades"][0]
    pay = client.get(f"/api/positions/{t['position_id']}/payoff").json()
    premium = -t["entry_credit"] * 65
    assert pay["max_loss"] == pytest.approx(premium)
    assert min(pay["expiry"]) == pytest.approx(-premium)               # between the strikes
    assert len(pay["breakevens"]) == 2
    assert pay["stop"] == pytest.approx(-4.6 * 65) and pay["target"] == pytest.approx(7.7 * 65)
    assert pay["now"] is not None                                    # closed trade: drawn at entry


def test_journal_performance_and_rules(client):
    j = client.get("/api/strategies/strangle_scalp/journal", params={**Q, "end": str(DAY)}).json()
    assert j["days"][0]["date"] == str(DAY) and len(j["days"][0]["trades"]) == 1
    p = client.get("/api/strategies/strangle_scalp/performance", params={**Q, "end": str(DAY)}).json()
    assert p["stats"]["trades"] == 1 and p["stats"]["win_rate"] == 100.0
    assert p["stats"]["return_pct"] == pytest.approx(p["stats"]["net"] / 12000 * 100, abs=0.01)
    assert p["by_dte"][0]["key"] == 0 and p["equity"][0][0] == str(DAY)
    r = client.get("/api/strategies/strangle_scalp/rules").json()
    keys = {row["key"]: row["value"] for row in r["rows"]}
    assert keys["exits.target_points"] == 7.7 and "max_trades" in keys
    assert "Long strangle scalp" in r["spec_text"] or "long strangle scalp" in r["spec_text"].lower()


def test_notes_can_be_added_edited_and_deleted(client):
    day = client.get(f"/api/strategies/strangle_scalp/day?date={DAY}", params=Q).json()
    pid = day["trades"][0]["position_id"]
    n = client.post("/api/notes", json=dict(algo_id="strangle_scalp", trade_date=str(DAY), position_id=pid,
                                            text="  Fast burst, clean fill  ", tags=["Burst", "burst", " "])).json()
    assert n["text"] == "Fast burst, clean fill" and n["tags"] == ["burst"]
    assert client.get(f"/api/positions/{pid}").json()["notes"][0]["id"] == n["id"]
    u = client.put(f"/api/notes/{n['id']}", json=dict(text="edited", tags=["x"])).json()
    assert u["text"] == "edited" and u["tags"] == ["x"]
    assert client.get(f"/api/strategies/strangle_scalp/day?date={DAY}", params=Q).json()["notes"][0]["text"] == "edited"
    assert client.delete(f"/api/notes/{n['id']}").status_code == 204
    assert client.delete(f"/api/notes/{n['id']}").status_code == 404
    assert client.post("/api/notes", json=dict(algo_id="strangle_scalp", trade_date=str(DAY), text=" ")).status_code == 400
    assert client.post("/api/notes", json=dict(algo_id="nope", trade_date=str(DAY), text="x")).status_code == 404


def test_unknown_strategy_or_position_is_404(client):
    assert client.get("/api/strategies/nope/day").status_code == 404
    assert client.get("/api/positions/999999").status_code == 404
    assert client.get("/api/positions/999999/payoff").status_code == 404
