from datetime import date, datetime

from dashboard import swing_queries as sq
from swing.zones import Touch, Zone, ZoneReport
from trading_core.clock import IST
from trading_core.db import models as m

TODAY = date(2026, 11, 7)


def report(zones=()):
    return ZoneReport("AAA", TODAY, 100.0, 2.0, 500, list(zones))


def add_results(db, symbol, day, text="To consider the results"):
    with db.session() as s:
        s.add(m.CalendarEvent(source="nse_results", event_key=f"{symbol}|{day}", category="results",
                              ts=datetime.combine(day, datetime.min.time(), tzinfo=IST), all_day=True, country="IN",
                              title=f"{symbol} results", impact="Low", symbol=symbol, detail={"description": text},
                              fetched_at=datetime(2026, 11, 5, tzinfo=IST)))


def test_results_and_zone_events_newest_first(db):
    add_results(db, "AAA", date(2026, 11, 20))
    add_results(db, "AAA", date(2026, 8, 1))
    add_results(db, "OTHER", date(2026, 11, 21))
    z = Zone(90.0, 95.0, [], touches=[Touch(date(2026, 10, 5), "support", 2.25), Touch(date(2024, 1, 5), "support", 1.0)],
             breaks=[(date(2026, 9, 1), "down"), (date(2024, 1, 9), "up")])
    items = sq.timeline(db, "AAA", report([z]), TODAY)
    assert [(i["day"], i["kind"]) for i in items] == [("2026-11-20", "results"), ("2026-10-05", "hold"),
                                                      ("2026-09-01", "break"), ("2026-08-01", "results")]
    assert items[0]["title"] == "Results" and "consider" in items[0]["detail"]
    assert items[1]["title"] == "Held as support at 90.00–95.00" and items[1]["detail"] == "Moved 2.2 ATR away"
    assert items[2]["title"] == "Broke down through 90.00–95.00"


def test_a_hold_with_no_move_measured_yet_says_nothing(db):
    z = Zone(90.0, 95.0, [], touches=[Touch(date(2026, 11, 6), "support", 0.0)])
    assert sq.timeline(db, "AAA", report([z]), TODAY)[0]["detail"] == ""


def test_empty_timeline(db):
    assert sq.timeline(db, "AAA", report(), TODAY) == []
