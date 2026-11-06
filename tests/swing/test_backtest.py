import csv
from datetime import date

from swing.backtest import BacktestConfig, resolve, run, summary, symbol_events, weekends, write
from swing.zones import ZoneConfig
from tests.swing.test_zones import make, saw

BT = BacktestConfig(horizon_days=10)


def test_weekends_are_each_weeks_last_trading_day():
    b = make([(1, 2, 1.5)] * 12)                 # Mon 5 Jan 2026 .. Tue 20 Jan
    assert [b.dates[i] for i in weekends(b.dates)] == [date(2026, 1, 9), date(2026, 1, 16), date(2026, 1, 20)]


def test_resolve_support():
    # zone 100-102, ATR 2: held at a high >= 104, broken at a low <= 98
    held = make([(101, 103, 102.5), (102, 104.5, 104), (103, 105, 104)])
    assert resolve(held, 0, "support", 100, 102, 2.0, BT) == ("held", 1)
    broken = make([(101, 103, 102.5), (97.5, 103, 98)])
    assert resolve(broken, 0, "support", 100, 102, 2.0, BT) == ("broken", 1)
    both = make([(101, 103, 102.5), (97, 105, 100)])
    assert resolve(both, 0, "support", 100, 102, 2.0, BT)[0] == "broken"
    reach_day_high = make([(101, 106, 101.5), (100.5, 103, 101)])        # high came before the touch
    assert resolve(reach_day_high, 0, "support", 100, 102, 2.0, BT) == ("open", 1)


def test_resolve_resistance():
    held = make([(99, 101, 99.5), (95.5, 99, 96)])
    assert resolve(held, 0, "resistance", 100, 102, 2.0, BT) == ("held", 1)
    broken = make([(99, 101, 99.5), (100, 104.5, 104)])
    assert resolve(broken, 0, "resistance", 100, 102, 2.0, BT) == ("broken", 1)


def test_events_on_a_range_use_only_the_past():
    b = make(saw(700))
    zc = ZoneConfig()
    ev = symbol_events(b, zc, BacktestConfig(), b.dates[300], b.dates[-1])
    zones = [e for e in ev if not e.random]
    assert zones and any(e.random for e in ev)
    assert all(e.reach_day > e.asof for e in ev)
    held = [e for e in zones if e.outcome == "held"]
    assert len(held) > 0.8 * sum(e.outcome != "open" for e in zones)   # a clean range holds
    assert ev == symbol_events(b, zc, BacktestConfig(), b.dates[300], b.dates[-1])   # seeded: repeatable


def test_run_summary_and_files(tmp_path):
    bars = {"A": make(saw(600)), "B": make(saw(600, 200, 220, 30))}
    for s, b in bars.items():
        b.symbol = s
    ev = run(bars, ZoneConfig(), BacktestConfig(), date(2027, 1, 1), date(2029, 12, 31), progress=lambda _: None)
    assert {e.symbol for e in ev} == {"A", "B"}
    text = summary(ev)
    assert "random bands" in text and "score 60-70" in text and "touches high" in text
    folder = write(ev, tmp_path / "bt", "header")
    rows = list(csv.DictReader(open(folder / "events.csv", encoding="utf-8")))
    assert len(rows) == len(ev) and "score" in rows[0]
    assert rows[0]["touches"].isdigit() and "part_touches" in rows[0]
    assert (folder / "summary.txt").read_text(encoding="utf-8").startswith("header")
