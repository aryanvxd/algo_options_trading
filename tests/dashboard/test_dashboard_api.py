from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from dashboard import queries
from dashboard.app import create_app
from tests.helpers import seed_day
from trading_core.clock import IST, SimClock, ist
from trading_core.db.repo import Journal
from trading_core.market.calendar import MarketCalendar
from trading_core.strategy.bootstrap import run_algo
from trading_core.strategy.position import Leg, Mark, Position

DAY = date(2026, 10, 23)
EXPIRY = date(2026, 10, 27)
FAST_TARGET = {"exits": {"pnl_basis": "gross", "stop_loss_points": 77, "target_points": 4.6,  # ≈ ₹5,000 / ₹300 on 1 lot
                         "stale_feed_seconds": 60}}


@pytest.fixture
def replayed(platform, db):
    seed_day(db, platform, DAY, EXPIRY, spot=lambda f: 25000.0, decay=lambda f: max(0.05, 1.0 - 2.0 * f))
    run_id = run_algo(platform, db, "iron_condor", replay_date=DAY, overrides=FAST_TARGET,
                      calendar=MarketCalendar([]))
    return run_id


def test_overview_groups_day_by_strategy(db, replayed):
    ov = queries.overview(db, DAY, "paper", "replay")
    assert ov["totals"]["trades"] == 1 and ov["totals"]["strategies"] == 1
    s = ov["strategies"][0]
    assert s["algo_id"] == "iron_condor" and s["run_id"] == replayed and s["status"] == "DONE"
    assert s["exit_reason"] == "TARGET"
    assert s["net"] == pytest.approx(s["gross"] - s["charges"])
    assert ov["totals"]["net"] == pytest.approx(s["net"])
    assert len(s["intraday"]) > 10 and s["intraday"][0][0] >= "09:20"
    # sessions (source=live) only contain the synthetic recorder, which never traded
    live = queries.overview(db, DAY, "paper", "live")
    assert all(x["trades"] == 0 for x in live["strategies"])


def test_history_and_cumulative(db, replayed):
    h = queries.pnl_history(db, DAY, 30, "paper", "replay")
    assert h["dates"] == [DAY.isoformat()]
    assert h["series"][0]["algo_id"] == "iron_condor"
    assert h["cumulative"] == [h["series"][0]["values"][0]]
    assert queries.pnl_history(db, DAY, 30, "live", "replay")["dates"] == []


def test_open_position_uses_live_state(db):
    j = Journal(db)
    now = ist(DAY, time(10, 0))
    j.register_algo("iron_condor", "NIFTY Short Iron Condor", "0", "", now)
    run_id = j.create_run("iron_condor", DAY, "paper", "live", {}, now)
    pos = Position("IRON_CONDOR", "NIFTY", [Leg("SHORT_CE", 1, "X", "SELL", 65, entry_price=10.0)], status="OPEN")
    j.create_position(run_id, "iron_condor", "paper", pos)
    j.update_position(pos)
    j.set_run_status(run_id, "IN_TRADE", now)
    j.upsert_live_state(run_id, now, "IN_TRADE", 25010.0, 13.5,
                        Mark(now, 9.0, 65.0, 25.0, 24.0, True), None, True)

    ov = queries.overview(db, DAY, "all", "live")
    s = ov["strategies"][0]
    assert s["open_positions"] == 1 and s["gross"] == 65.0 and s["net"] == 16.0 and s["charges"] == 49.0
    assert ov["totals"]["open_net"] == 16.0 and ov["totals"]["running"] == 1


def test_http_endpoints(db, replayed):
    client = TestClient(create_app(db, SimClock(datetime(2026, 10, 23, 16, 0, tzinfo=IST))))
    assert "Algo Desk" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/chartjs/chart.umd.min.js").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["today"] == "2026-10-23" and meta["latest_replay_date"] == "2026-10-23"
    r = client.get("/api/overview", params={"source": "replay"}).json()
    assert r["date"] == "2026-10-23" and r["totals"]["trades"] == 1
    assert client.get("/api/overview", params={"date": "23-10-2026"}).status_code == 400
    assert client.get("/api/overview", params={"mode": "bogus"}).status_code == 422
    assert client.get("/api/pnl-history", params={"source": "replay", "days": 7}).status_code == 200
