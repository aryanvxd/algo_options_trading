import json
import os
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from dashboard import control
from dashboard.app import create_app
from tests.algos.strangle_scalp.test_scalp_replay import DAY, burst, run, seed
from trading_core.clock import IST, SimClock, ist
from trading_core.db import models as m
from trading_core.db.repo import Journal
from trading_core.reporting.daily_report import report_dir

WRITE = {"X-Algo-Desk": "1"}


def logged_in(db, now):
    with db.session() as s:
        s.add(m.BrokerSession(user_id="AB1234", access_token="t", login_at=now - timedelta(hours=1),
                              valid_until=now + timedelta(hours=12)))


def pid_file(platform, algo_id, pid):
    path = platform.data_dir / "locks" / f"{algo_id}.pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(pid=pid, started_at="2026-10-26T09:35:00+05:30")))


@pytest.fixture
def launched():
    return []


@pytest.fixture
def app_at(platform, db, launched):
    def make(when):
        return TestClient(create_app(db, SimClock(when), platform=platform,
                                     launcher=lambda args, cwd, title: launched.append((args, title))))
    return make


MONDAY_0930 = datetime(2026, 10, 26, 9, 30, tzinfo=IST)


def test_start_needs_a_kite_session_then_launches_algo_run(app_at, db, launched):
    client = app_at(MONDAY_0930)
    st = client.get("/api/control").json()
    assert not st["kite"]["logged_in"] and st["algos"]["strangle_scalp"]["start_blocked"] == "log in to Kite first"
    r = client.post("/api/strategies/strangle_scalp/start", headers=WRITE)
    assert r.status_code == 409 and "log in to Kite" in r.json()["detail"] and launched == []

    logged_in(db, MONDAY_0930)
    assert client.get("/api/control").json()["algos"]["strangle_scalp"]["can_start"]
    r = client.post("/api/strategies/strangle_scalp/start", headers=WRITE)
    assert r.status_code == 200 and r.json() == dict(started="strangle_scalp", resume=False, entry_closed=False)
    assert launched == [(["run", "--algo", "strangle_scalp"], "AlgoDesk-strangle_scalp")]


def test_running_algo_shows_stop_and_stop_sends_kill(app_at, platform, db):
    client = app_at(MONDAY_0930)
    assert client.post("/api/strategies/strangle_scalp/stop", headers=WRITE).status_code == 409   # not running
    pid_file(platform, "strangle_scalp", os.getpid())             # this test process is alive (and Python)
    a = client.get("/api/control").json()["algos"]["strangle_scalp"]
    assert a["running"] and a["pid"] == os.getpid() and not a["can_start"]
    assert client.post("/api/strategies/strangle_scalp/start", headers=WRITE).status_code == 409
    assert client.post("/api/strategies/strangle_scalp/stop", headers=WRITE).status_code == 200
    assert Journal(db).pending_commands("strangle_scalp", 1, MONDAY_0930) == ["KILL"]


def test_dead_process_in_a_stale_pid_file_is_not_running(platform):
    pid_file(platform, "strangle_scalp", 2 ** 22 + 12345)
    assert control.running(platform, "strangle_scalp") is None


def test_finished_day_and_late_start_are_reported(app_at, platform, db):
    logged_in(db, ist(DAY, time(10, 40)))
    seed(db, platform, burst(15 * 60, 80, 60))
    run_id = run(platform, db)
    with db.session() as s:                                       # make the replay look like today's live run
        s.get(m.AlgoRun, run_id).source = "live"
    a = app_at(ist(DAY, time(10, 40))).get("/api/control").json()["algos"]["strangle_scalp"]
    assert not a["can_start"] and a["start_blocked"].startswith("already finished today")
    assert a["entry_closed"]                                      # 10:40 is past the 10:30 entry deadline
    late = app_at(ist(DAY, time(10, 46))).get("/api/control").json()["algos"]["iron_condor"]
    assert late["can_start"] and late["entry_closed"]             # condor: square-off is 15:15


def test_writes_need_the_dashboard_header_and_a_local_origin(app_at):
    client = app_at(MONDAY_0930)
    assert client.post("/api/kite/login").status_code == 403
    assert client.post("/api/kite/login", headers={**WRITE, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/notes", json={}).status_code == 403


def test_login_opens_algo_login(app_at, launched):
    assert app_at(MONDAY_0930).post("/api/kite/login", headers=WRITE).status_code == 200
    assert launched == [(["login"], "AlgoDesk-login")]


# ---------------------------------------------------------------- stale commands

def test_commands_sent_before_the_process_started_are_dropped(db):
    j = Journal(db)
    start = datetime(2026, 10, 29, 9, 35, tzinfo=IST)
    j.issue_command("iron_condor", "KILL", start - timedelta(hours=20))      # yesterday, nothing running
    assert j.pending_commands("iron_condor", 7, start, since=start) == []
    j.issue_command("iron_condor", "KILL", start + timedelta(minutes=5))
    assert j.pending_commands("iron_condor", 7, start + timedelta(minutes=6), since=start) == ["KILL"]
    assert j.pending_commands("iron_condor", 7, start + timedelta(minutes=7), since=start) == []   # consumed


# ---------------------------------------------------------------- several runs on one day

def test_a_second_run_on_the_same_day_adds_up_instead_of_hiding_the_first(platform, db):
    from dashboard import queries
    from dashboard import strategy_queries as sq

    seed(db, platform, burst(15 * 60, 80, 60))
    first = run(platform, db)
    second = run(platform, db, {"schedule": {"entry": "10:31"}})   # starts after the window: no trade
    with db.session() as s:
        for rid in (first, second):
            s.get(m.AlgoRun, rid).source = "live"
    day = sq.strategy_day(db, "strangle_scalp", DAY, "paper", "live")
    assert [r["run_id"] for r in day["runs"]] == [first, second]
    assert [r["trades"] for r in day["runs"]] == [1, 0]
    assert day["run"]["run_id"] == first and day["run"]["skip_reason"] is None and len(day["trades"]) == 1
    [card] = [c for c in queries.overview(db, DAY, "paper", "live")["strategies"] if c["algo_id"] == "strangle_scalp"]
    assert card["trades"] == 1 and card["run_count"] == 2 and card["status"] == "DONE"
    j = sq.journal(db, "strangle_scalp", DAY, DAY, "paper", "live")
    assert len(j["days"]) == 1 and len(j["days"][0]["runs"]) == 2 and len(j["days"][0]["trades"]) == 1


def test_second_live_run_gets_its_own_report_folder(platform):
    base = dict(trade_date=DAY, source="live", algo_id="strangle_scalp")
    first = report_dir(platform.reports_dir, dict(base, run_id=3))
    first.mkdir(parents=True)
    (first / "summary.json").write_text(json.dumps({"run_id": 3}))
    assert report_dir(platform.reports_dir, dict(base, run_id=3)) == first
    assert report_dir(platform.reports_dir, dict(base, run_id=4)).name == f"{DAY}_run4"
