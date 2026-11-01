from datetime import date, timedelta

import numpy as np
import pytest

from swing.bars import Bars
from swing.zones import (Pivot, Zone, ZoneConfig, _touches_and_breaks, atr_series, find_zones, group,
                         merge_timeframes, pivots, round_step)

CFG = ZoneConfig()


def weekdays(n: int, start: date = date(2026, 1, 5)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make(rows: list[tuple[float, float, float]], volume: float = 1000.0, start: date = date(2026, 1, 5)) -> Bars:
    """Bars from (low, high, close) rows; open = close."""
    lo, hi, cl = (np.array(c, dtype=float) for c in zip(*rows))
    return Bars("TEST", weekdays(len(rows), start), cl.copy(), hi, lo, cl, np.full(len(rows), volume))


def test_atr_series():
    b = make([(9, 11, 10), (10, 13, 12), (11, 12, 11)])
    # true ranges: 2, max(3, |13-10|, |10-10|) = 3, max(1, |12-12|, |11-12|) = 1
    assert list(atr_series(b, 2)) == [2.0, 2.5, 2.0]


def test_pivots_need_k_bars_each_side_and_ties_count_once():
    rows = [(10 - i, 12 - i, 11 - i) for i in range(6)] + [(5 + i, 7 + i, 6 + i) for i in range(6)]
    b = make(rows)                                   # lows 10..5 then 5..10: two days at 5
    p = pivots(b, 3, "d")
    assert [(x.kind, x.price, x.day) for x in p] == [("L", 5.0, b.dates[5])]
    assert pivots(b, 6, "d") == []                   # not 6 bars on both sides


def test_weekly_pivot_at_a_daily_pivot_is_one_turn():
    d = [Pivot(100.0, "L", date(2026, 1, 7), "d"), Pivot(110.0, "H", date(2026, 2, 4), "d")]
    w = [Pivot(100.0, "L", date(2026, 1, 9), "w"), Pivot(120.0, "H", date(2026, 3, 6), "w")]
    out = merge_timeframes(d, w)
    assert [(p.price, p.tf) for p in out] == [(100.0, "dw"), (110.0, "d"), (120.0, "w")]


def piv(*prices):
    return [Pivot(p, "L", date(2026, 1, 1), "d") for p in prices]


def test_group_splits_at_gaps():
    g = group(piv(10, 10.4, 10.8, 12, 12.2), atr=1.0, gap_atr=0.5, max_width_atr=2.5)
    assert [[p.price for p in x] for x in g] == [[10, 10.4, 10.8], [12, 12.2]]


def test_group_splits_wide_chain_at_widest_gap():
    g = group(piv(10, 10.4, 10.8, 11.2, 11.6, 12.05, 12.45, 12.85), atr=1.0, gap_atr=0.5, max_width_atr=2.5)
    assert [[p.price for p in x] for x in g] == [[10, 10.4, 10.8, 11.2, 11.6], [12.05, 12.45, 12.85]]


def test_touches_breaks_and_flip():
    rows = ([(105, 107, 106)] * 3                     # above the zone
            + [(100.5, 102, 101)] * 4                 # a long visit inside (like POWERGRID, Oct 2026)
            + [(101, 104, 103.5)]                     # closes back above: held (touch 1)
            + [(101.5, 103.5, 102.5)]                 # a poke before moving 1 ATR away: not counted
            + [(106, 109, 108)]                       # 1 ATR away: the next test counts
            + [(101, 103, 102.8)]                     # touch 2
            + [(96, 98, 97)] * 2                      # 2 closes more than 1 ATR below: broken down
            + [(99, 101, 99.5)])                      # back up into it, closes below: resistance (flip)
    b = make(rows)
    z = Zone(100.0, 102.0, [])
    _touches_and_breaks(z, b, np.full(len(b), 2.0), CFG)
    assert [(t.side, t.day) for t in z.touches] == [("support", b.dates[3]), ("support", b.dates[10]),
                                                    ("resistance", b.dates[13])]
    assert z.touches[0].departure_atr == pytest.approx((109 - 102) / 2)   # best high of the next 5 days
    assert z.breaks == [(b.dates[12], "down")]
    from swing.zones import _flipped
    assert _flipped(z)


def test_going_through_is_not_a_touch():
    b = make([(105, 107, 106), (101, 103, 101.5), (97, 101, 98), (96, 99, 97)])
    z = Zone(100.0, 102.0, [])
    _touches_and_breaks(z, b, np.full(len(b), 2.0), CFG)
    assert z.touches == []


def saw(days: int, lo: float = 100.0, hi: float = 110.0, period: int = 20) -> list[tuple[float, float, float]]:
    """Price swinging between lo and hi: clear support at lo and resistance at hi."""
    out = []
    for i in range(days):
        x = (i % period) / (period / 2)
        c = lo + (hi - lo) * (x if x <= 1 else 2 - x)
        out.append((c - 0.5, c + 0.5, c))
    return out


def test_find_zones_on_a_range():
    rows = saw(400) + [(104.5, 105.5, 105.0)]
    rep = find_zones(make(rows), CFG)
    assert rep.note == "" and rep.close == 105.0
    sup, res = rep.supports(), rep.resistances()
    assert sup and res
    assert sup[0].lo <= 99.5 <= sup[0].hi and res[0].lo <= 110.5 <= res[0].hi
    assert len(sup[0].touches) >= CFG.full_touches and all(t.side == "support" for t in sup[0].touches)
    assert 0 < sup[0].score <= 100 and "round 100" in sup[0].confluence


def test_asof_uses_only_earlier_candles():
    b = make(saw(400) + [(80, 81, 80.5)] * 20)       # a crash after day 400
    asof = b.dates[399]
    rep = find_zones(b, CFG, asof)
    assert rep.asof == asof and rep.close == b.close[399]
    assert all(p.day <= asof for z in rep.zones for p in z.pivots)
    assert all(z.lo > 90 for z in rep.zones)


def test_short_history_has_no_zones():
    rep = find_zones(make(saw(100)), CFG)
    assert rep.zones == [] and "100 daily candles" in rep.note


def test_round_step():
    assert round_step(265) == 50 and round_step(1500) == 500 and round_step(45) == 5
