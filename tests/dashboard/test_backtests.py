"""Backtests in the dashboard (docs/design/backtest.md, section 6).

F-B1: the sidebar sub-menu and routes. F-B2: listing and reading backtest folders, and the trash."""

import json
import re
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from dashboard import backtests as bt
from dashboard import control
from dashboard.app import create_app
from dashboard.strategy_queries import _exit_points
from trading_core.clock import IST, SimClock
from trading_core.db import models as m
from trading_core.db.engine import Database

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=IST)
WRITE = {"X-Algo-Desk": "1"}
SNAPSHOT = {"algo_id": "iron_condor_balanced", "version": "0.2.0", "lots": 2,
            "exits": {"stop_by_dte": {"0": 5, "1": 8, "2": 15}, "target_by_dte": {"0": 5, "1": 8, "2": 15}}}


def make_run(platform, name, state="done", *, trades=True, summary=True, **status):
    """A backtest folder as the engine writes it: status.json, summary.json and backtest.db with one traded
    day (5 Nov, DTE 1, stopped out) and one skipped day (6 Nov)."""
    folder = platform.data_dir / "backtests" / name
    folder.mkdir(parents=True)
    body = dict(name=name, algo_id="iron_condor_balanced", version="0.2.0", start="2026-11-04", end="2026-11-06",
                days=3, option_prices="real", overrides={"exits": {"stop_by_dte": {"0": 5, "1": 8, "2": 12}}},
                backtest_overrides={}, started_at="2026-11-07T20:00:00+05:30", state=state, done=3,
                updated_at="2026-11-07T20:01:00+05:30", elapsed_s=60)
    (folder / "status.json").write_text(json.dumps({**body, **status}), encoding="utf-8")
    if summary:
        (folder / "summary.json").write_text(json.dumps(dict(
            meta=body, totals=dict(trades=1, net=-1408.69, wins=0, losses=1, win_rate=0.0, profit_factor=0.0,
                                   expectancy=-1408.69, max_drawdown=-1408.69, days=2, days_traded=1, days_skipped=1),
            by_month={"2026-11": dict(trades=1, net=-1408.69)}, equity=[], skips={},
            coverage=dict(days=3, missing_options=["2026-11-04"], missing_index=[]), limits=["x"])), encoding="utf-8")
    if trades:
        db = Database(f"sqlite:///{(folder / 'backtest.db').as_posix()}")
        db.create_all()
        with db.session() as s:
            s.add(m.Algo(algo_id="iron_condor_balanced", name="Balanced", created_at=NOW))
            for run_id, d, st, why in ((1, date(2026, 11, 5), "DONE", None), (2, date(2026, 11, 6), "SKIPPED", "VIX 21 above 20")):
                s.add(m.AlgoRun(run_id=run_id, algo_id="iron_condor_balanced", trade_date=d, mode="paper", source="replay",
                                status=st, skip_reason=why, config_snapshot=SNAPSHOT, config_hash="x",
                                started_at=datetime(d.year, d.month, d.day, 9, 15)))
            s.flush()
            p = m.Position(position_id=7, run_id=1, algo_id="iron_condor_balanced", mode="paper", structure="IRON_CONDOR",
                           underlying="NIFTY", expiry=date(2026, 11, 9), status="CLOSED",
                           entry_time=datetime(2026, 11, 5, 9, 50), exit_time=datetime(2026, 11, 5, 11, 0),
                           entry_credit_pts=40, exit_debit_pts=48, exit_reason="STOP_LOSS", gross_pnl=-1040,
                           charges=368.69, net_pnl=-1408.69, params={"dte": 1, "spot": 25500})
            s.add(p)
            s.flush()
            for role, k, opt, side, px in (("SHORT_CE", 26000, "CE", "SELL", 30), ("LONG_CE", 26300, "CE", "BUY", 8),
                                           ("SHORT_PE", 25000, "PE", "SELL", 25), ("LONG_PE", 24700, "PE", "BUY", 7)):
                s.add(m.Leg(position_id=7, role=role, instrument_token=k, tradingsymbol=f"NIFTY{k}{opt}", option_type=opt,
                            strike=k, side=side, qty=130, entry_price=px, exit_price=px))
        db.engine.dispose()
    return folder


@pytest.fixture
def client(platform, db):
    return TestClient(create_app(db, SimClock(NOW), platform=platform))


# ---------------------------------------------------------------- F-B1: sidebar

def test_backtests_submenu_is_wired_in(client):
    html = client.get("/").text
    for page in ("backtests.js", "backtest_new.js", "backtest_compare.js", "backtest_sweep.js", "backtest_data.js", "backtests.css"):
        assert f"/static/pages/{page}" in html
        assert client.get(f"/static/pages/{page}").status_code == 200
    # Backtests opens its sub-menu; it is no longer a disabled "Soon" item.
    assert 'data-submenu="backtests"' in html and "Backtests (soon)" not in html
    assert 'id="nav-back"' in html
    for view in ("new", "sweep", "runs", "compare", "data"):
        assert f'data-page="backtests/{view}"' in html


def test_every_nav_section_can_collapse(client):
    html = client.get("/").text
    groups = re.findall(r'class="nav-group[^"]*" data-group="([\w-]+)"', html)
    assert {"strategies", "markets", "research", "system", "bt-run", "bt-results", "bt-data", "bt-recent"} <= set(groups)
    assert len(groups) == len(set(groups))           # each section's open/closed state has its own key
    assert html.count('aria-expanded="true"') == len(groups)


# ---------------------------------------------------------------- F-B2: list and read

def test_list_skips_studies_and_the_trash_newest_first(client, platform):
    make_run(platform, "older", started_at="2026-11-01T20:00:00+05:30")
    make_run(platform, "newer")
    (platform.data_dir / "backtests" / "condor_balance_study" / "scripts").mkdir(parents=True)   # no status.json
    (platform.data_dir / "backtests" / ".trash" / "gone").mkdir(parents=True)
    runs = client.get("/api/backtests").json()["runs"]
    assert [r["name"] for r in runs] == ["newer", "older"]
    r = runs[0]
    assert r["state"] == "done" and r["option_prices"] == "real" and r["missing_options"] == 1
    assert r["totals"]["net"] == -1408.69 and r["totals"]["profit_factor"] == 0.0
    assert r["overrides"]["exits"]["stop_by_dte"]["2"] == 12


def test_list_with_no_backtests_folder(client):
    assert client.get("/api/backtests").json() == {"runs": []}


def test_running_stale_and_failed_states(client, platform, monkeypatch):
    make_run(platform, "fresh", "running", summary=False, done=1, updated_at="2026-11-08T17:59:00+05:30")
    make_run(platform, "old_no_pid", "running", summary=False, done=432, days=492, updated_at="2026-11-04T08:31:26+05:30")
    make_run(platform, "dead_pid", "running", summary=False, done=2, pid=999999, updated_at="2026-11-08T17:59:59+05:30")
    make_run(platform, "broke", "failed", summary=False, error="ValueError('bad')")
    monkeypatch.setattr(control, "_alive", lambda pid: False)
    runs = {r["name"]: r for r in client.get("/api/backtests").json()["runs"]}
    assert (runs["fresh"]["state"], runs["fresh"]["label"]) == ("running", "day 1 of 3")
    assert (runs["old_no_pid"]["state"], runs["old_no_pid"]["label"]) == ("stale", "stopped at day 432 of 492")
    assert runs["dead_pid"]["state"] == "stale"
    assert runs["broke"]["state"] == "failed" and "bad" in runs["broke"]["label"]
    assert runs["fresh"]["totals"] is None


def test_detail_has_summary_snapshot_and_live_config(client, platform):
    make_run(platform, "r1")
    d = client.get("/api/backtests/r1").json()
    assert d["summary"]["totals"]["trades"] == 1
    assert d["config_snapshot"]["exits"]["stop_by_dte"]["2"] == 15
    assert d["live_config"]["algo_id"] == "iron_condor_balanced"       # today's config, for the Settings diff
    assert d["has_log"] is False


@pytest.mark.parametrize("name", ["nope", "..", ".trash", "a b", "x" * 81])
def test_unknown_or_unsafe_names_are_404(client, platform, name):
    make_run(platform, "r1")
    assert client.get(f"/api/backtests/{name}").status_code == 404
    assert client.get(f"/api/backtests/{name}/days").status_code == 404


def test_days_lists_traded_skipped_and_not_run(client, platform):
    make_run(platform, "r1")
    days = {d["date"]: d for d in client.get("/api/backtests/r1/days").json()["days"]}
    assert list(days) == ["2026-11-06", "2026-11-05", "2026-11-04"]
    assert days["2026-11-05"]["net"] == -1408.69 and days["2026-11-05"]["dte"] == 1 and days["2026-11-05"]["exit"] == "STOP_LOSS"
    assert days["2026-11-06"]["skip_reason"] == "VIX 21 above 20" and days["2026-11-06"]["trades"] == 0
    assert days["2026-11-04"]["status"] == "NOT_RUN" and "option candles" in days["2026-11-04"]["skip_reason"]


def test_strategy_queries_read_the_runs_own_db(client, platform):
    make_run(platform, "r1")
    trades = client.get("/api/backtests/r1/trades").json()
    assert (trades["start"], trades["end"]) == ("2026-11-04", "2026-11-06")      # the run's range by default
    assert [t["position_id"] for t in trades["trades"]] == [7]
    assert client.get("/api/backtests/r1/performance").json()["by_dte"][0]["key"] == 1
    journal = client.get("/api/backtests/r1/journal", params={"start": "2026-11-06", "end": "2026-11-06"}).json()
    assert [d["date"] for d in journal["days"]] == ["2026-11-06"]
    assert client.get("/api/backtests/r1/positions/7").json()["trade"]["net"] == -1408.69
    pay = client.get("/api/backtests/r1/positions/7/payoff").json()
    assert pay["stop"] == -8 * 130 and pay["target"] == 8 * 130      # stop_by_dte at the trade's DTE (1)
    assert client.get("/api/backtests/r1/positions/99").status_code == 404


def test_a_queued_run_has_no_results_and_no_db_is_created(client, platform):
    folder = make_run(platform, "q1", "queued", trades=False, summary=False, done=0)
    assert client.get("/api/backtests/q1/trades").status_code == 409
    assert not (folder / "backtest.db").exists()
    assert client.get("/api/backtests/q1/days").json()["days"] == []


def test_log_tail(client, platform):
    folder = make_run(platform, "r1")
    assert client.get("/api/backtests/r1/log").json()["available"] is False
    (folder / "run.log").write_text("\n".join(f"line {i}" for i in range(300)), encoding="utf-8")
    out = client.get("/api/backtests/r1/log", params={"lines": 5}).json()
    assert out["available"] and out["lines"] == [f"line {i}" for i in range(295, 300)]


# ---------------------------------------------------------------- F-B2: trash

def test_trash_moves_the_folder_never_deletes(client, platform):
    make_run(platform, "r1")
    client.get("/api/backtests/r1/trades")                     # leaves the run's db open in the cache
    assert client.post("/api/backtests/r1/trash").status_code == 403      # writes need the dashboard header
    out = client.post("/api/backtests/r1/trash", headers=WRITE).json()
    moved = platform.data_dir / out["to"]
    assert not (platform.data_dir / "backtests" / "r1").exists()
    assert (moved / "backtest.db").exists() and moved.parent.name == ".trash" and moved.name == "r1_20261108-180000"
    assert client.get("/api/backtests").json()["runs"] == []
    assert client.post("/api/backtests/r1/trash", headers=WRITE).status_code == 404


def test_trash_refuses_a_running_run(client, platform, monkeypatch):
    make_run(platform, "live1", "running", pid=4242, summary=False)
    monkeypatch.setattr(control, "_alive", lambda pid: True)
    r = client.post("/api/backtests/live1/trash", headers=WRITE)
    assert r.status_code == 409 and "stop it" in r.json()["detail"]
    assert (platform.data_dir / "backtests" / "live1").exists()


def test_exit_points_picks_like_the_algo():
    exits = {"stop_by_dte": {"0": 5, "2": 15}}
    assert _exit_points(exits, "stop_loss_points", "stop_by_dte", 1) == 5      # largest key at or below
    assert _exit_points(exits, "stop_loss_points", "stop_by_dte", 4) == 15
    assert _exit_points({"stop_loss_points": 77}, "stop_loss_points", "stop_by_dte", 1) == 77
    assert _exit_points({"stop_loss_points": None}, "stop_loss_points", "stop_by_dte", None) is None


# ---------------------------------------------------------------- F-B3: the form's config

def test_algo_config_for_the_form(client):
    c = client.get("/api/algos/iron_condor_balanced/config").json()
    assert c["values"]["exits"]["stop_by_dte"]["2"] == 15 and c["trade_window_days"] == 5
    assert "$defs" in c["schema"] and "exits" in c["schema"]["properties"]
    assert "Trailing profit stop" in c["help"]["exits.trail_arm_by_dte"]          # comment block above the key
    assert c["help"]["filters.vix_max"].startswith("India VIX must be below")      # comment after the value
    assert c["order"].index("exits.stop_by_dte") < c["order"].index("schedule.entry")
    assert client.get("/api/algos/nope/config").status_code == 404


def test_yaml_help_ignores_hash_in_quotes_and_resets_on_blank_lines(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('# file header\n\nname: "a # b"   # the name\n# about exits\nexits:\n  # stop\n  stop: 5\n\n  # orphan\n\n'
                 '  target: 8 # tgt\n', encoding="utf-8")
    help_, order = bt.yaml_help(p)
    assert help_ == {"name": "the name", "exits": "about exits", "exits.stop": "stop", "exits.target": "tgt"}
    assert order == ["name", "exits", "exits.stop", "exits.target"]


def test_json_overrides_replace_yaml_dte_keys():
    from trading_core.config import deep_merge, int_keys
    from trading_core.strategy import registry

    _, cfg = registry.load("iron_condor_balanced", deep_merge({}, int_keys({"exits": {"stop_by_dte": {"2": 12}}})))
    assert cfg.exits.stop_by_dte == {0: 5, 1: 8, 2: 12, 3: 15, 4: 15}             # replaced, not added beside


def test_cli_overrides_file_is_applied_after_set(tmp_path, monkeypatch, platform):
    from trading_core import cli
    from trading_core.backtest import engine

    seen = {}

    def fake_run(platform_, algo, start, end, **kw):
        seen.update(kw, algo=algo)
        raise SystemExit(0)

    monkeypatch.setattr(engine, "run_backtest", fake_run)
    f = tmp_path / "o.json"
    f.write_text(json.dumps({"schedule": {"entry": "09:53"}, "exits": {"stop_by_dte": {"2": 12}}}), encoding="utf-8")
    args = cli.build_parser().parse_args(["backtest", "--algo", "iron_condor_balanced", "--from", "2026-11-06",
                                          "--set", "lots=1", "--set", "schedule.entry=09:50", "--overrides-file", str(f)])
    with pytest.raises(SystemExit):
        cli.cmd_backtest(args, platform)
    assert seen["overrides"] == {"lots": 1, "schedule": {"entry": "09:53"}, "exits": {"stop_by_dte": {2: 12}}}


# ---------------------------------------------------------------- F-B3: queueing, the worker, stop

class FakeProc:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        return self.returncode


class FakeSpawn:
    def __init__(self):
        self.started, self.procs = [], []

    def __call__(self, platform, folder, req):
        self.started.append((folder.name, req))
        self.procs.append(FakeProc(5000 + len(self.procs)))
        return self.procs[-1]


@pytest.fixture
def queue(platform):
    killed = []
    q = bt.Queue(platform, spawn=FakeSpawn(), kill=killed.append)
    q.killed = killed
    return q


@pytest.fixture
def qclient(platform, db, queue):
    return TestClient(create_app(db, SimClock(NOW), platform=platform, backtest_queue=queue))


def req(name="icb_new", **kw):
    return {"algo_id": "iron_condor_balanced", "start": "2026-11-05", "end": "2026-11-06", "name": name,
            "option_prices": "real", "overrides": {"exits": {"stop_by_dte": {"0": 5, "1": 10, "2": 15, "3": 15, "4": 15}}},
            "days": 2, **kw}


def test_submit_queues_a_folder(qclient, platform):
    r = qclient.post("/api/backtests", json=req(), headers=WRITE)
    assert r.status_code == 201 and r.json()["state"] == "queued"
    folder = platform.data_dir / "backtests" / "icb_new"
    assert json.loads((folder / "overrides.json").read_text())["exits"]["stop_by_dte"]["1"] == 10
    assert json.loads((folder / "request.json").read_text())["queued_at"].startswith("2026-11-08T18:00")
    status = json.loads((folder / "status.json").read_text())
    assert status["state"] == "queued" and status["version"] == "0.2.0" and status["days"] == 2
    assert not (folder / "backtest.db").exists()
    assert qclient.post("/api/backtests", json=req()).status_code == 403                        # header needed
    assert qclient.post("/api/backtests", json=req(), headers=WRITE).status_code == 409          # name taken


@pytest.mark.parametrize("change, words", [
    ({"name": "bad name"}, "name:"),
    ({"algo_id": "nope"}, "unknown strategy"),
    ({"end": "2026-11-09"}, "future"),
    ({"start": "2026-11-07", "end": "2026-11-06"}, "after end"),
    ({"option_prices": "both"}, "real or model"),
    ({"overrides": {"mode": "live"}}, "change mode"),
    ({"overrides": {"exits": {"stop_by_dte": {"2": "lots"}}}}, "invalid settings"),
    ({"not_before": "after lunch"}, "not_before"),
])
def test_submit_refuses_bad_requests(qclient, platform, change, words):
    r = qclient.post("/api/backtests", json=req(**change), headers=WRITE)
    assert r.status_code == 400 and words in r.json()["detail"]
    assert not (platform.data_dir / "backtests" / req(**change)["name"]).exists()


def test_worker_runs_one_at_a_time_in_order(qclient, queue, platform):
    for n in ("first", "second"):
        assert qclient.post("/api/backtests", json=req(n), headers=WRITE).status_code == 201
    assert queue.spawn.started == []                     # the worker isn't running in tests: nothing starts by itself
    assert queue.tick(NOW) == "first"
    st = json.loads((platform.data_dir / "backtests" / "first" / "status.json").read_text())
    assert st["state"] == "running" and st["pid"] == 5000
    assert queue.spawn.started[0][1]["overrides"]["exits"]["stop_by_dte"]["1"] == 10
    assert queue.tick(NOW) is None                       # still running
    queue.spawn.procs[0].returncode = 1                  # died without writing its outcome
    assert queue.tick(NOW) == "second"
    first = json.loads((platform.data_dir / "backtests" / "first" / "status.json").read_text())
    assert first["state"] == "failed" and "exit code 1" in first["error"]


def test_worker_waits_for_not_before_and_for_terminal_runs(qclient, queue, platform, monkeypatch):
    qclient.post("/api/backtests", json=req("later", not_before="2026-11-08T18:30:00+05:30"), headers=WRITE)
    assert queue.tick(NOW) is None
    later = datetime(2026, 11, 8, 18, 31, tzinfo=IST)
    make_run(platform, "from_terminal", "running", summary=False, pid=4242, updated_at="2026-11-08T18:30:59+05:30")
    monkeypatch.setattr(control, "_alive", lambda pid: True)
    assert queue.tick(later) is None                     # a backtest started in the terminal is using the CPU
    monkeypatch.setattr(control, "_alive", lambda pid: False)
    assert queue.tick(later) == "later"


def test_stop_cancels_queued_and_ends_running(qclient, queue, platform, monkeypatch):
    qclient.post("/api/backtests", json=req("a"), headers=WRITE)
    qclient.post("/api/backtests", json=req("b"), headers=WRITE)
    assert qclient.post("/api/backtests/b/stop", headers=WRITE).json() == {"cancelled": "b"}
    b = json.loads((platform.data_dir / "backtests" / "b" / "status.json").read_text())
    assert b["error"] == "cancelled before it started"
    queue.tick(NOW)
    monkeypatch.setattr(control, "_alive", lambda pid: True)
    assert qclient.post("/api/backtests/a/stop", headers=WRITE).json() == {"stopped": "a"}
    assert queue.killed == [5000]
    st = json.loads((platform.data_dir / "backtests" / "a" / "status.json").read_text())
    assert st["state"] == "failed" and st["error"].startswith("stopped from the dashboard")
    assert qclient.post("/api/backtests/a/stop", headers=WRITE).status_code == 409    # not running any more
    assert qclient.post("/api/backtests/a/trash", headers=WRITE).status_code == 200   # stopped runs can go


def test_stop_refuses_a_terminal_run(qclient, platform, monkeypatch):
    make_run(platform, "cli_run", "running", summary=False, pid=4242)
    monkeypatch.setattr(control, "_alive", lambda pid: True)
    r = qclient.post("/api/backtests/cli_run/stop", headers=WRITE)
    assert r.status_code == 409 and "terminal" in r.json()["detail"]


def test_dry_run_only_checks(qclient, platform):
    r = qclient.post("/api/backtests", json=req("dry", dry_run=True), headers=WRITE)
    assert r.status_code == 201 and r.json()["ok"] is True
    assert not (platform.data_dir / "backtests" / "dry").exists()
    bad = qclient.post("/api/backtests", json=req("dry", dry_run=True, overrides={"lots": "two"}), headers=WRITE)
    assert bad.status_code == 400 and "invalid settings" in bad.json()["detail"]


def test_sweep_runs_carry_their_point(qclient, platform):
    sweep = {"name": "sw1", "axes": [{"path": "exits.stop_by_dte[2]"}], "point": {"exits.stop_by_dte[2]": 12}, "index": 1, "total": 2}
    assert qclient.post("/api/backtests", json=req("sw1_01", sweep=sweep), headers=WRITE).status_code == 201
    row = qclient.get("/api/backtests").json()["runs"][0]
    assert row["sweep"]["name"] == "sw1" and row["sweep"]["point"] == {"exits.stop_by_dte[2]": 12}
    assert "dry_run" not in json.loads((platform.data_dir / "backtests" / "sw1_01" / "request.json").read_text())


# ---------------------------------------------------------------- F-B6: coverage and downloads

def seed_files(db, rows):
    with db.session() as s:
        for ds, key, src, d, status in rows:
            s.add(m.MarketDataFile(dataset=ds, partition_key=key, source=src, trade_date=d, status=status,
                                   path="x", rows=1, sha256="0", written_at=NOW))


def test_coverage_finds_gaps_and_expiry_states(client, db):
    seed_files(db, [
        ("index_1m", "NIFTY 50", "kite", date(2026, 11, 2), "OK"),
        ("index_1m", "NIFTY 50", "kite", date(2026, 11, 3), "EMPTY"),         # a stored empty day: a holiday
        ("index_1m", "NIFTY 50", "kite", date(2026, 11, 6), "OK"),            # 4 and 5 Nov missing
        ("equity_1m", "SBIN", "kite", date(2026, 11, 2), "OK"), ("equity_1m", "SBIN", "kite", date(2026, 11, 6), "OK"),
        ("equity_1m", "TCS", "kite", date(2026, 11, 6), "OK"),
        ("options_1m", "NIFTY:2026-11-03", "kite", date(2026, 11, 3), "OK"),  # saved through its expiry day
        ("options_1m", "NIFTY:2026-10-27", "kite", date(2026, 10, 23), "OK"),  # its expiry day was lost
        ("options_1m", "NIFTY:2026-11-09", "kite", date(2026, 11, 6), "OK"),
        ("options_1m", "NIFTY:2026-11-09", "kite", date(2026, 11, 5), "PARTIAL"),
    ])
    c = client.get("/api/data/coverage").json()
    nifty = c["index"][0]
    assert (nifty["first"], nifty["last"], nifty["days"]) == ("2026-11-02", "2026-11-06", 2)
    assert nifty["gaps"] == ["2026-11-04", "2026-11-05"] and nifty["gap_count"] == 2
    assert c["stocks"]["count"] == 2 and c["stocks"]["short"] == [{"symbol": "TCS", "days": 1}]
    states = {e["expiry"]: e["state"] for e in c["expiries"]}
    assert states == {"2026-11-09": "collecting", "2026-11-03": "complete", "2026-10-27": "incomplete"}
    assert next(e for e in c["expiries"] if e["expiry"] == "2026-11-09")["problems"] == ["2026-11-05"]
    assert set(c["downloads"]) == {"options", "index", "futures", "constituents-1m", "bhavcopy"}
    assert "args" not in c["downloads"]["options"]


class FakeDownload:
    def __init__(self):
        self.calls, self.proc = [], None

    def __call__(self, platform, args, log):
        self.calls.append((args, log))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line 1\nline 2\n", encoding="utf-8")
        self.proc = FakeProc(7000 + len(self.calls))
        return self.proc


@pytest.fixture
def dclient(platform, db, monkeypatch):
    from dashboard import data_coverage

    fake = FakeDownload()
    jobs = data_coverage.DataJobs(platform, spawn=fake)
    kite = {"logged_in": True}                       # never asks the real Kite
    monkeypatch.setattr(control, "status", lambda *a, **k: {"kite": dict(kite)})
    c = TestClient(create_app(db, SimClock(NOW), platform=platform, data_jobs=jobs))
    c.fake, c.kite = fake, kite
    return c


def test_download_starts_one_at_a_time_from_the_last_stored_day(dclient, db):
    seed_files(db, [("options_eod", "NIFTY", "nse", date(2026, 10, 30), "OK")])
    assert dclient.post("/api/data/bhavcopy").status_code == 403                         # writes need the header
    r = dclient.post("/api/data/bhavcopy", headers=WRITE)
    assert r.status_code == 201 and r.json()["args"] == ["data", "bhavcopy", "--from", "2026-10-30"]
    assert dclient.post("/api/data/options", headers=WRITE).status_code == 409             # one at a time
    job = dclient.get("/api/data/jobs").json()["job"]
    assert job["running"] and job["lines"][-1] == "line 2" and job["log"].endswith(".log")
    dclient.fake.proc.returncode = 0
    assert dclient.get("/api/data/jobs").json()["job"]["running"] is False
    r = dclient.post("/api/data/index", json={"from": "2026-11-01"}, headers=WRITE)
    assert r.json()["args"] == ["data", "index", "--from", "2026-11-01"]
    dclient.fake.proc.returncode = 0
    assert dclient.post("/api/data/options", headers=WRITE).json()["args"] == ["data", "options"]   # no --from


def test_kite_downloads_need_the_login(dclient):
    dclient.kite["logged_in"] = False
    r = dclient.post("/api/data/options", headers=WRITE)
    assert r.status_code == 409 and "log in to Kite" in r.json()["detail"]
    assert dclient.post("/api/data/bhavcopy", headers=WRITE).status_code == 201           # NSE: no login needed
    assert dclient.fake.calls[0][0][:2] == ["data", "bhavcopy"]
    assert dclient.post("/api/data/nope", headers=WRITE).status_code == 404


def test_data_days_with_an_empty_store(client):
    out = client.get("/api/data/days").json()
    assert out["trading_days"] == [] and out["real_option_days"] == [] and out["last_day"] is None
