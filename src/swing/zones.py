"""Support and resistance zones from daily and weekly candles (spec section 4, Z1-Z9).

Everything is computed from candles up to ``asof`` only, so the backtest (W3) can call the same
function week by week without look-ahead.

Z1 pivots: a daily low/high that is the extreme of ``pivot_daily`` bars either side; weekly the
   same with ``pivot_weekly``. A weekly pivot at the same price in the same week as a daily one is
   the same turn, so it is kept once (marked as both).
Z2 grouping: pivot prices sorted; a gap of ``group_gap_atr`` x ATR or more starts a new zone, and
   a zone wider than ``max_width_atr`` is split at its widest internal gap. (A plain "within one
   ATR" chain joins every level when pivots are dense, as POWERGRID's are.)
Z3 touches, Z5 departure, Z9 breaks, Z4 role reversal, Z6 freshness, Z7 volume at price and Z8
   confluence are measured on the zones; the score is their weighted mean, 0-100.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from pydantic import BaseModel, ConfigDict

from swing.bars import Bars, weekly


class ZoneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: int = 730            # pivots and touches from the last two years
    min_history_days: int = 250         # fewer daily candles than this: no zones (recent listings)
    atr_days: int = 14
    pivot_daily: int = 5                # Z1
    pivot_weekly: int = 3
    group_gap_atr: float = 0.5          # Z2
    max_width_atr: float = 2.5
    min_width_atr: float = 0.5
    min_turns: int = 2                  # a zone needs this many pivots, or this many touches
    retest_atr: float = 1.0             # Z3: a new test from a side once price was this far away
    departure_days: int = 5             # Z5
    break_atr: float = 1.0              # Z9: closes this far beyond ...
    break_days: int = 2                 # ... this many days running
    fresh_days: int = 60                # Z6: tests in the last 60 sessions count as recent
    stale_tests: int = 4                # ... this many recent tests scores 0
    full_touches: int = 4               # Z3 scores 1 at this many touches
    full_departure_atr: float = 3.0     # Z5 scores 1 at this average departure
    confluence_pad_atr: float = 0.25    # Z8: a level this close to the zone counts
    weights: dict[str, float] = {"touches": 1, "reversal": 1, "departure": 1, "freshness": 1,
                                 "volume": 1, "confluence": 1}


@dataclass
class Pivot:
    price: float
    kind: str                # "L" low / "H" high
    day: date
    tf: str                  # "d", "w" or "dw"


@dataclass
class Touch:
    day: date
    side: str                # "support" (came from above, turned up) / "resistance"
    departure_atr: float


@dataclass
class Zone:
    lo: float
    hi: float
    pivots: list[Pivot]
    touches: list[Touch] = field(default_factory=list)
    breaks: list[tuple[date, str]] = field(default_factory=list)     # (day, "down"/"up")
    role: str = ""           # vs the last close: "support" (below), "resistance" (above), "inside"
    flipped: bool = False
    recent_tests: int = 0
    volume_ratio: float | None = None
    confluence: list[str] = field(default_factory=list)
    parts: dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    def count(self, side: str) -> int:
        return sum(t.side == side for t in self.touches)

    @property
    def last_test(self) -> date | None:
        return self.touches[-1].day if self.touches else None


@dataclass
class ZoneReport:
    symbol: str
    asof: date
    close: float
    atr: float
    days: int
    zones: list[Zone]
    note: str = ""

    def supports(self) -> list[Zone]:
        """Below the close, nearest first."""
        return sorted((z for z in self.zones if z.role == "support"), key=lambda z: -z.hi)

    def resistances(self) -> list[Zone]:
        """Above the close, nearest first."""
        return sorted((z for z in self.zones if z.role == "resistance"), key=lambda z: z.lo)


def atr_series(b: Bars, n: int) -> np.ndarray:
    """Simple n-day average of the true range, per bar (the first bars average what they have)."""
    if len(b) == 0:
        return np.zeros(0)
    prev = np.concatenate([[b.close[0]], b.close[:-1]])
    tr = np.maximum(b.high - b.low, np.maximum(np.abs(b.high - prev), np.abs(b.low - prev)))
    c = np.concatenate([[0.0], np.cumsum(tr)])
    i = np.arange(1, len(tr) + 1)
    lo = np.maximum(0, i - n)
    return (c[i] - c[lo]) / (i - lo)


def pivots(b: Bars, k: int, tf: str) -> list[Pivot]:
    """Extremes of ``k`` bars either side. Equal extremes (two days at the same low) are one turn:
    the first is kept."""
    out = []
    for i in range(k, len(b) - k):
        if b.low[i] < b.low[i - k:i].min() and b.low[i] <= b.low[i + 1:i + k + 1].min():
            out.append(Pivot(float(b.low[i]), "L", b.dates[i], tf))
        if b.high[i] > b.high[i - k:i].max() and b.high[i] >= b.high[i + 1:i + k + 1].max():
            out.append(Pivot(float(b.high[i]), "H", b.dates[i], tf))
    return out


def merge_timeframes(daily: list[Pivot], wk: list[Pivot]) -> list[Pivot]:
    """Daily and weekly pivots count equally; one turn seen on both is kept once, as "dw"."""
    by_key = {(round(p.price, 2), p.kind, p.day.isocalendar()[:2]): p for p in daily}
    out = list(daily)
    for p in wk:
        twin = by_key.get((round(p.price, 2), p.kind, p.day.isocalendar()[:2]))
        if twin:
            twin.tf = "dw"
        else:
            out.append(p)
    return sorted(out, key=lambda p: (p.price, p.day))


def group(piv: list[Pivot], atr: float, gap_atr: float, max_width_atr: float) -> list[list[Pivot]]:
    """Z2: split the price-sorted pivots at gaps >= gap_atr x ATR, then split any group wider than
    max_width_atr x ATR at its widest gap until none is."""
    if not piv:
        return []
    groups, cur = [], [piv[0]]
    for a, b in zip(piv, piv[1:]):
        if b.price - a.price >= gap_atr * atr:
            groups.append(cur)
            cur = []
        cur.append(b)
    groups.append(cur)
    out, stack = [], groups[::-1]
    while stack:
        g = stack.pop()
        if len(g) > 1 and g[-1].price - g[0].price > max_width_atr * atr:
            k = int(np.argmax(np.diff([p.price for p in g]))) + 1
            stack += [g[k:], g[:k]]
        else:
            out.append(g)
    return out


def _touches_and_breaks(z: Zone, b: Bars, atr: np.ndarray, cfg: ZoneConfig) -> None:
    """Z3, Z5, Z9 over the window's daily bars.

    A visit starts when a bar reaches into the zone from one side and ends at the first close
    outside it. Out the same side = a test that held (a touch), however long price sat inside:
    POWERGRID spent three weeks of Oct 2026 inside 242-252 before leaving upward. Out the other
    side = it went through. A new touch from a side counts only after price has been
    ``retest_atr`` away on that side, so pokes at the edge aren't counted again and again."""
    n = len(b)
    side: str | None = None                             # side of the last close outside the zone
    visit: tuple[str, int] | None = None
    armed = {"above": True, "below": True}
    for i in range(n):
        if side is None:
            side = "above" if b.close[i] > z.hi else "below" if b.close[i] < z.lo else None
            continue
        if visit is None and ((side == "above" and b.low[i] <= z.hi) or (side == "below" and b.high[i] >= z.lo)):
            visit = (side, i)
        if visit is None:
            if side == "above" and b.high[i] > z.hi + cfg.retest_atr * atr[i]:
                armed["above"] = True
            if side == "below" and b.low[i] < z.lo - cfg.retest_atr * atr[i]:
                armed["below"] = True
            continue
        if z.lo <= b.close[i] <= z.hi:
            continue                                    # still inside
        came, start = visit
        out = "above" if b.close[i] > z.hi else "below"
        if out == came and armed[came]:
            span = slice(start, i + 1)
            if came == "above":
                k = start + int(np.argmin(b.low[span]))
                after = b.high[i + 1:i + 1 + cfg.departure_days]
                dep = (after.max() - z.hi) / atr[i] if len(after) else 0.0
                z.touches.append(Touch(b.dates[k], "support", max(0.0, float(dep))))
            else:
                k = start + int(np.argmax(b.high[span]))
                after = b.low[i + 1:i + 1 + cfg.departure_days]
                dep = (z.lo - after.min()) / atr[i] if len(after) else 0.0
                z.touches.append(Touch(b.dates[k], "resistance", max(0.0, float(dep))))
            armed[came] = False
        visit, side = None, out
    side, run = None, 0
    for i in range(n):
        beyond = ("below" if b.close[i] < z.lo - cfg.break_atr * atr[i]
                  else "above" if b.close[i] > z.hi + cfg.break_atr * atr[i] else None)
        if side is None:
            side = "above" if b.close[i] > z.hi else "below" if b.close[i] < z.lo else None
        if beyond and side and beyond != side:
            run += 1
            if run >= cfg.break_days:
                z.breaks.append((b.dates[i], "down" if beyond == "below" else "up"))
                side, run = beyond, 0
        else:
            run = 0
            if beyond:
                side = beyond


def _flipped(z: Zone) -> bool:
    """Z4: held as support, broke down, then held as resistance (or the reverse, upward)."""
    for day, way in z.breaks:
        before, after = ("support", "resistance") if way == "down" else ("resistance", "support")
        if (any(t.side == before and t.day <= day for t in z.touches)
                and any(t.side == after and t.day > day for t in z.touches)):
            return True
    return False


def _volume_profile(b: Bars, atr: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Z7: each day's volume spread evenly over its range, in bins of 0.1 ATR."""
    if not b.volume.any():
        return None                                      # indices have no volume
    step = max(atr / 10, 1e-9)
    lo0 = b.low.min()
    edges = lo0 + step * np.arange(int(math.ceil((b.high.max() - lo0) / step)) + 2)
    vol = np.zeros(len(edges) - 1)
    for lo, hi, v in zip(b.low, b.high, b.volume):
        a = int((lo - lo0) // step)
        c = int((hi - lo0) // step)
        vol[a:c + 1] += v / (c - a + 1)
    return edges, vol


def round_step(price: float) -> float:
    """₹250 / ₹1,000 style round numbers: half of the price's leading power of ten."""
    return 10 ** math.floor(math.log10(max(price, 1e-9))) / 2


def _confluence(z: Zone, full: Bars, atr: float, cfg: ZoneConfig) -> list[str]:
    pad = cfg.confluence_pad_atr * atr
    near = lambda x: z.lo - pad <= x <= z.hi + pad  # noqa: E731
    out = []
    if len(full) >= 200 and near(float(full.close[-200:].mean())):
        out.append("200-day avg")
    step = round_step(z.mid)
    r = math.ceil((z.lo - pad) / step) * step
    if r <= z.hi + pad:
        out.append(f"round {r:,.0f}")
    year = full.since(full.dates[-1] - timedelta(days=365))
    if z.role != "resistance" and near(float(year.low.min())):
        out.append("52-week low")
    if z.role != "support" and near(float(year.high.max())):
        out.append("52-week high")
    return out


def _score(z: Zone, cfg: ZoneConfig) -> None:
    deps = [t.departure_atr for t in z.touches]
    parts = {
        "touches": min(1.0, len(z.touches) / cfg.full_touches),
        "reversal": 1.0 if z.flipped else 0.0,
        "departure": min(1.0, (sum(deps) / len(deps)) / cfg.full_departure_atr) if deps else 0.0,
        "freshness": max(0.0, 1 - max(0, z.recent_tests - 1) / max(1, cfg.stale_tests - 1)),
        "confluence": min(1.0, len(z.confluence) / 3),
    }
    if z.volume_ratio is not None:
        parts["volume"] = min(1.0, z.volume_ratio / 2)
    w = {k: cfg.weights.get(k, 0.0) for k in parts}
    total = sum(w.values())
    z.parts = parts
    z.score = round(100 * sum(parts[k] * w[k] for k in parts) / total, 1) if total else 0.0


def find_zones(full: Bars, cfg: ZoneConfig, asof: date | None = None) -> ZoneReport:
    """Zones for one instrument from its daily candles up to ``asof`` (default: the last day)."""
    if asof is not None:
        i = next((k for k, d in enumerate(full.dates) if d > asof), len(full))
        full = Bars(full.symbol, full.dates[:i], full.open[:i], full.high[:i], full.low[:i],
                    full.close[:i], full.volume[:i])
    if len(full) < cfg.min_history_days:
        return ZoneReport(full.symbol, full.dates[-1] if len(full) else asof or date.min,
                          float(full.close[-1]) if len(full) else 0.0, 0.0, len(full), [],
                          note=f"only {len(full)} daily candles (need {cfg.min_history_days})")
    day = full.dates[-1]
    win = full.since(day - timedelta(days=cfg.lookback_days))
    atr = atr_series(win, cfg.atr_days)
    atr_now, close = float(atr[-1]), float(win.close[-1])
    piv = merge_timeframes(pivots(win, cfg.pivot_daily, "d"), pivots(weekly(win), cfg.pivot_weekly, "w"))
    zones = []
    for g in group(piv, atr_now, cfg.group_gap_atr, cfg.max_width_atr):
        lo, hi = g[0].price, g[-1].price
        if hi - lo < cfg.min_width_atr * atr_now:
            mid = (lo + hi) / 2
            lo, hi = mid - cfg.min_width_atr * atr_now / 2, mid + cfg.min_width_atr * atr_now / 2
        zones.append(Zone(round(lo, 2), round(hi, 2), sorted(g, key=lambda p: p.day)))
    profile = _volume_profile(win, atr_now)
    if profile is not None:
        edges, vol = profile
        mean_density = vol.sum() / (edges[-1] - edges[0])
    recent_from = win.dates[max(0, len(win) - cfg.fresh_days)]
    for z in zones:
        _touches_and_breaks(z, win, atr, cfg)
        z.role = "support" if z.hi < close else "resistance" if z.lo > close else "inside"
        z.flipped = _flipped(z)
        z.recent_tests = sum(t.day >= recent_from for t in z.touches)
        if profile is not None:
            mids = (edges[:-1] + edges[1:]) / 2
            inside = (mids >= z.lo) & (mids <= z.hi)
            width = max(z.hi - z.lo, 1e-9)
            z.volume_ratio = round(float(vol[inside].sum() / width / mean_density), 2) if mean_density else None
        z.confluence = _confluence(z, full, atr_now, cfg)
        _score(z, cfg)
    zones = [z for z in zones if len(z.pivots) >= cfg.min_turns or len(z.touches) >= cfg.min_turns]
    return ZoneReport(full.symbol, day, close, round(atr_now, 2), len(full), zones)
