"""Zone backtest (spec section 9, first test): do the zones hold, and do higher scores hold more?

Walk forward week by week. At each week's last trading day (the "weekend"), find zones from the
candles up to that day only. Every zone that price reaches during the following week is an event:

- **held**: after reaching it, price goes ``move_atr`` x ATR beyond the zone's near edge (above the
  top of a support, below the bottom of a resistance) before going ``move_atr`` x ATR through the
  far edge;
- **broken**: through the far edge first (or both on the same day: counted as broken);
- **open**: neither within ``horizon_days`` sessions.

On the day price reaches the zone only "broken" is checked: that day's other extreme may have come
before the touch. ATR is the one at the weekend.

The baseline: each weekend, random bands with the widths of that stock's zones, at a random
distance (0 to ``random_max_atr`` ATR) below and above the close, judged by the same rules. If
zones don't hold more often than random bands, or high scores don't beat low ones, the score is
wrong.

Uses today's member lists, so older years carry some survivorship bias (stocks that fell out of
the indices aren't in the test).
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from pydantic import BaseModel, ConfigDict

from swing.bars import Bars, avg_traded_value_cr
from swing.zones import ZoneConfig, find_zones

PARTS = ("touches", "reversal", "departure", "freshness", "volume", "confluence")
SCORE_BUCKETS = (0, 40, 50, 60, 70, 80, 101)


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    move_atr: float = 1.0               # held / broken threshold
    horizon_days: int = 60              # sessions to resolve an event
    step_weeks: int = 1                 # every weekend
    random_per_side: int = 2            # random bands per weekend per side
    random_max_atr: float = 3.0
    seed: int = 7


@dataclass
class ZoneEvent:
    symbol: str
    asof: date
    side: str                # "support" / "resistance"
    lo: float
    hi: float
    atr: float
    close: float
    dist_atr: float          # from the close to the zone's near edge
    reach_day: date
    outcome: str             # "held" / "broken" / "open"
    days: int                # sessions from reach to outcome
    random: bool = False
    on_zone: bool = False    # a random band overlapping one of that weekend's zones
    score: float = 0.0
    touches: int = 0
    flipped: bool = False
    parts: dict[str, float] = field(default_factory=dict)


def weekends(dates: list[date]) -> list[int]:
    """Index of each ISO week's last trading day."""
    out = []
    for i, d in enumerate(dates):
        if i + 1 == len(dates) or dates[i + 1].isocalendar()[:2] != d.isocalendar()[:2]:
            out.append(i)
    return out


def resolve(b: Bars, k: int, side: str, lo: float, hi: float, atr: float, bt: BacktestConfig) -> tuple[str, int]:
    """Outcome of a zone reached on bar ``k``."""
    up_lvl, dn_lvl = (hi + bt.move_atr * atr, lo - bt.move_atr * atr)
    for j in range(k, min(len(b), k + 1 + bt.horizon_days)):
        if side == "support":
            if b.low[j] <= dn_lvl:
                return "broken", j - k
            if j > k and b.high[j] >= up_lvl:
                return "held", j - k
        else:
            if b.high[j] >= up_lvl:
                return "broken", j - k
            if j > k and b.low[j] <= dn_lvl:
                return "held", j - k
    return "open", min(len(b) - 1, k + bt.horizon_days) - k


def _reach(b: Bars, start: int, stop: int, side: str, lo: float, hi: float) -> int | None:
    for k in range(start, stop):
        if (side == "support" and b.low[k] <= hi) or (side == "resistance" and b.high[k] >= lo):
            return k
    return None


def symbol_events(b: Bars, zcfg: ZoneConfig, bt: BacktestConfig, start: date, end: date,
                  min_traded_value_cr: float = 0.0) -> list[ZoneEvent]:
    rng = random.Random(f"{bt.seed}:{b.symbol}")
    ends = weekends(b.dates)
    out: list[ZoneEvent] = []
    for n, a in enumerate(ends[:-1]):
        day = b.dates[a]
        if day < start or day > end or n % bt.step_weeks:
            continue
        if min_traded_value_cr and b.volume[: a + 1].any():
            upto = Bars(b.symbol, b.dates[: a + 1], b.open[: a + 1], b.high[: a + 1], b.low[: a + 1],
                        b.close[: a + 1], b.volume[: a + 1])
            if avg_traded_value_cr(upto) < min_traded_value_cr:
                continue
        rep = find_zones(b, zcfg, day)
        if rep.note or not rep.zones or rep.atr <= 0:
            continue
        nxt = ends[n + 1] + 1                      # the following week: bars a+1 .. next weekend
        for z in rep.zones:
            if z.role not in ("support", "resistance"):
                continue
            k = _reach(b, a + 1, nxt, z.role, z.lo, z.hi)
            if k is None:
                continue
            outcome, days = resolve(b, k, z.role, z.lo, z.hi, rep.atr, bt)
            dist = (rep.close - z.hi) if z.role == "support" else (z.lo - rep.close)
            out.append(ZoneEvent(b.symbol, day, z.role, z.lo, z.hi, rep.atr, rep.close, round(dist / rep.atr, 2),
                                 b.dates[k], outcome, days, score=z.score, touches=len(z.touches),
                                 flipped=z.flipped, parts=dict(z.parts)))
        widths = [z.hi - z.lo for z in rep.zones]
        for side in ("support", "resistance"):
            for _ in range(bt.random_per_side):
                w = rng.choice(widths)
                d = rng.uniform(0, bt.random_max_atr) * rep.atr
                lo, hi = (rep.close - d - w, rep.close - d) if side == "support" else (rep.close + d, rep.close + d + w)
                k = _reach(b, a + 1, nxt, side, lo, hi)
                if k is None:
                    continue
                outcome, days = resolve(b, k, side, lo, hi, rep.atr, bt)
                on_zone = any(z.lo <= hi and z.hi >= lo for z in rep.zones)
                out.append(ZoneEvent(b.symbol, day, side, round(lo, 2), round(hi, 2), rep.atr, rep.close,
                                     round(d / rep.atr, 2), b.dates[k], outcome, days, random=True, on_zone=on_zone))
    return out


def _job(args) -> list[ZoneEvent]:
    return symbol_events(*args)


def run(bars: dict[str, Bars], zcfg: ZoneConfig, bt: BacktestConfig, start: date, end: date,
        min_traded_value_cr: float = 0.0, workers: int = 1,
        progress: Callable[[str], None] = print) -> list[ZoneEvent]:
    jobs = [(b, zcfg, bt, start, end, min_traded_value_cr) for b in bars.values()]
    out: list[ZoneEvent] = []
    if workers <= 1:
        results: Iterable[list[ZoneEvent]] = map(_job, jobs)
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        results = pool.map(_job, jobs, chunksize=4)
    for i, ev in enumerate(results, 1):
        out += ev
        if i % 25 == 0 or i == len(jobs):
            progress(f"{i}/{len(jobs)} symbols, {len(out):,} events")
    if workers > 1:
        pool.shutdown()
    return out


# ---------------------------------------------------------------- summary

def _rate(evs: list[ZoneEvent]) -> tuple[int, float, float, float]:
    """events, held % and broken % of the resolved ones, open % of all."""
    n = len(evs)
    held = sum(e.outcome == "held" for e in evs)
    broken = sum(e.outcome == "broken" for e in evs)
    done = held + broken
    return n, (100 * held / done if done else 0.0), (100 * broken / done if done else 0.0), \
        (100 * (n - done) / n if n else 0.0)


def _row(label: str, evs: list[ZoneEvent]) -> str:
    n, h, b, o = _rate(evs)
    return f"{label:<26}{n:>8,}{h:>8.1f}{b:>8.1f}{o:>8.1f}"


def summary(events: list[ZoneEvent]) -> str:
    head = f"{'':<26}{'events':>8}{'held%':>8}{'broke%':>8}{'open%':>8}"
    zones = [e for e in events if not e.random]
    rand = [e for e in events if e.random]
    lines = ["Held% and broke% are of the resolved events; open% is of all.", ""]
    for side in ("support", "resistance", None):
        zs = [e for e in zones if side in (None, e.side)]
        rs = [e for e in rand if side in (None, e.side)]
        lines += [f"== {side or 'all'} ==", head, _row("random bands", rs),
                  _row("  clear of every zone", [e for e in rs if not e.on_zone]),
                  _row("  overlapping a zone", [e for e in rs if e.on_zone]), _row("zones", zs)]
        for lo, hi in zip(SCORE_BUCKETS, SCORE_BUCKETS[1:]):
            lines.append(_row(f"  score {lo}-{min(hi, 100)}", [e for e in zs if lo <= e.score < hi]))
        lines.append("")
    lines += ["== each score part (all zones): part >= 0.5 vs < 0.5 ==", head]
    for p in PARTS:
        has = [e for e in zones if p in e.parts]
        lines.append(_row(f"  {p} high", [e for e in has if e.parts[p] >= 0.5]))
        lines.append(_row(f"  {p} low", [e for e in has if e.parts[p] < 0.5]))
    lines += ["", "== by distance from the close at the weekend (ATR) ==", head]
    for lo, hi in ((0, 0.5), (0.5, 1), (1, 2), (2, 99)):
        lines.append(_row(f"  zones {lo}-{hi}", [e for e in zones if lo <= e.dist_atr < hi]))
        lines.append(_row(f"  random {lo}-{hi}", [e for e in rand if lo <= e.dist_atr < hi]))
    by_year: dict[int, list[ZoneEvent]] = defaultdict(list)
    for e in zones:
        by_year[e.asof.year].append(e)
    lines += ["", "== zones by year ==", head]
    lines += [_row(f"  {y}", by_year[y]) for y in sorted(by_year)]
    return "\n".join(lines)


def write(events: list[ZoneEvent], folder: Path, header: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "events.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        cols = [f for f in asdict(events[0]) if f != "parts"] if events else []
        w.writerow(cols + [f"part_{p}" for p in PARTS])      # "touches" is also the event's count
        for e in events:
            d = asdict(e)
            w.writerow([d[c] for c in cols] + [e.parts.get(p, "") for p in PARTS])
    (folder / "summary.txt").write_text(header + "\n\n" + summary(events) + "\n", encoding="utf-8")
    return folder
