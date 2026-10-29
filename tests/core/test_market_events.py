from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from trading_core.clock import IST
from trading_core.db import models as m
from trading_core.market import events
from trading_core.market.calendar import MarketCalendar

NOW = datetime(2026, 10, 29, 9, 30, tzinfo=IST)

FF = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-30T08:30:00-04:00",
     "impact": "High", "forecast": "180K", "previous": "254K"},
    {"title": "German ifo Business Climate", "country": "EUR", "date": "2026-10-26T05:00:00-04:00",
     "impact": "Low", "forecast": "90.0", "previous": "89.9"},
    {"title": "Bank Holiday", "country": "NZD", "date": "2026-10-25T15:00:00-04:00", "impact": "Holiday",
     "forecast": "", "previous": ""},
]
NSE = [
    {"symbol": "RELIANCE", "company": "Reliance Industries Limited", "purpose": "Financial Results",
     "bm_desc": "To consider and approve the financial results", "date": "31-Oct-2026"},
    {"symbol": "AARON", "company": "Aaron Industries Limited", "purpose": "Financial Results/Other business matters",
     "bm_desc": "...", "date": "30-Oct-2026"},
    {"symbol": "XYZ", "company": "XYZ Ltd", "purpose": "Fund Raising", "bm_desc": "...", "date": "30-Oct-2026"},
]


def test_forexfactory_times_become_ist_and_currencies_countries():
    rows = events.parse_forexfactory(FF)
    nfp = rows[0]
    assert nfp["ts"] == datetime(2026, 10, 30, 18, 0, tzinfo=IST) and nfp["country"] == "US"   # 08:30 ET = 18:00 IST
    assert (nfp["impact"], nfp["forecast"], nfp["previous"], nfp["actual"]) == ("High", "180K", "254K", None)
    assert rows[2]["all_day"] and rows[2]["forecast"] is None


def test_nse_keeps_results_only_and_flags_index_members():
    rows = events.parse_nse_results(NSE, {"RELIANCE"})
    assert [r["symbol"] for r in rows] == ["RELIANCE", "AARON"]
    assert rows[0]["impact"] == "Medium" and rows[0]["detail"]["nifty50"]
    assert rows[1]["impact"] == "Low" and rows[0]["ts"].date() == date(2026, 10, 31)


def test_india_list_moves_weekend_releases_to_the_next_working_day(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "events:\n"
        "  - {date: 2026-12-12, time: '16:00', title: CPI, impact: High, source: mospi}\n"
        "  - {date: 2026-12-04, time: '10:00', title: RBI, impact: High, source: rbi}\n", encoding="utf-8")
    cpi, rbi = events.load_india([tmp_path / "x.yaml"], MarketCalendar([]))
    assert cpi["ts"] == datetime(2026, 12, 14, 16, 0, tzinfo=IST)        # Sat 12 Dec -> Mon 14 Dec
    assert cpi["detail"]["planned"] == "2026-12-12" and cpi["country"] == "IN"
    assert rbi["ts"] == datetime(2026, 12, 4, 10, 0, tzinfo=IST) and "planned" not in rbi["detail"]


def test_the_real_india_file_loads():
    from trading_core.config import load_platform_config

    p = load_platform_config()
    rows = events.load_india(sorted((p.config_dir / "calendar").glob("*.yaml")), MarketCalendar([]))
    titles = {r["title"] for r in rows}
    assert "RBI Monetary Policy Decision" in titles and any(t.startswith("CPI") for t in titles)
    assert all(r["impact"] in ("High", "Medium", "Low") for r in rows)


def test_refresh_fetches_once_a_day_survives_a_failing_source_and_upserts(platform, db):
    calls = []

    def ff():
        calls.append("ff")
        return FF

    def nse_down():
        raise OSError("NSE blocked us")

    out = events.refresh(db, platform, NOW, MarketCalendar([]), fetch_ff=ff, fetch_nse=nse_down)
    assert out["forexfactory"] == "3 events" and out["nse_results"].startswith("failed")
    assert out["india"].endswith("events")
    src = events.sources(db)
    assert src["forexfactory"]["status"] == "OK" and src["nse_results"]["status"] == "FAILED"

    out = events.refresh(db, platform, NOW, MarketCalendar([]), fetch_ff=ff, fetch_nse=lambda: NSE + NSE[:1])
    assert out["forexfactory"] == "already fetched today" and calls == ["ff"]   # (NSE lists RELIANCE twice)
    assert out["nse_results"] == "2 events"                                # a failed source is retried

    events.refresh(db, platform, NOW, MarketCalendar([]), force=True, fetch_ff=ff, fetch_nse=lambda: NSE)
    with db.session() as s:                                                # refetching never duplicates
        assert s.scalar(select(func.count()).select_from(m.CalendarEvent)
                        .where(m.CalendarEvent.source == "forexfactory")) == 3

    window = events.between(db, datetime(2026, 10, 30, 15, 30, tzinfo=IST), datetime(2026, 11, 2, 9, 15, tzinfo=IST),
                            min_impact="High", category="economic")
    assert [e.title for e in window] == ["Non-Farm Employment Change"]
    assert events.covered(db, NOW)["forexfactory"]


def test_weekend_condor_skips_a_weekend_with_us_jobs_data(platform, db):
    from tests.algos.weekend_condor.test_weekend_replay import FRI, friday, positions, replay, run_row

    friday(db, platform)
    events._store(db, "forexfactory", events.parse_forexfactory(FF[:1]), NOW)      # NFP, Fri 18:00 IST
    run_id = replay(platform, db, FRI)
    run, _ = run_row(db, run_id)
    assert run.status == "SKIPPED" and "Non-Farm Employment Change" in run.skip_reason
    assert positions(db) == []


def test_weekend_condor_skips_when_a_heavyweight_reports_on_saturday(platform, db):
    from tests.algos.weekend_condor.test_weekend_replay import FRI, friday, positions, replay, run_row

    friday(db, platform)
    events._store(db, "nse_results", events.parse_nse_results(NSE, {"RELIANCE"}), NOW)  # RELIANCE Sat 31 Oct
    run_id = replay(platform, db, FRI)
    run, _ = run_row(db, run_id)
    assert run.status == "SKIPPED" and "Reliance Industries Limited results" in run.skip_reason
    assert positions(db) == []


def test_a_low_impact_event_does_not_block_the_weekend(platform, db):
    from tests.algos.weekend_condor.test_weekend_replay import FRI, friday, positions, replay

    friday(db, platform)
    low = dict(FF[0], impact="Low", title="Some Minor Index")
    events._store(db, "forexfactory", events.parse_forexfactory([low]), NOW)
    replay(platform, db, FRI)
    [pos] = positions(db)
    assert pos.status == "OPEN"


def test_calendar_api_lists_events_holidays_expiries_and_algo_days(platform, db):
    from fastapi.testclient import TestClient

    from dashboard.app import create_app
    from tests.helpers import instrument_rows
    from trading_core.clock import SimClock
    from trading_core.db.migrate import seed_holidays
    from trading_core.kite.instruments import InstrumentStore

    seed_holidays(db, platform)
    InstrumentStore(db, platform.instruments).save_snapshot(date(2026, 10, 29), instrument_rows(date(2026, 11, 3)))
    events.refresh(db, platform, NOW, MarketCalendar([]), fetch_ff=lambda: FF, fetch_nse=lambda: NSE)
    client = TestClient(create_app(db, SimClock(NOW), platform=platform), headers={"X-Algo-Desk": "1"})
    cal = client.get("/api/calendar", params={"start": "2026-10-26", "days": 14}).json()
    assert {e["title"] for e in cal["economic"]} >= {"Non-Farm Employment Change", "German ifo Business Climate"}
    assert [e["symbol"] for e in cal["results"]] == ["AARON", "RELIANCE"]
    assert cal["expiries"] == [dict(date="2026-11-03", weekday="Tue", underlying="NIFTY", monthly=True)]
    fri = {a["algo_id"]: a["role"] for a in cal["algo_days"]["2026-10-30"]}
    mon = {a["algo_id"]: a["role"] for a in cal["algo_days"]["2026-11-02"]}
    assert fri == {"iron_condor": "trade", "weekend_condor": "entry"}           # scalp: Mon/Tue only
    assert mon == {"iron_condor": "trade", "strangle_scalp": "trade", "weekend_condor": "exit"}
    assert "2026-10-31" not in cal["algo_days"]                                    # Saturday
    assert cal["sources"]["forexfactory"]["status"] == "OK"
    assert client.post("/api/calendar/refresh", headers={"X-Algo-Desk": ""}).status_code == 403
