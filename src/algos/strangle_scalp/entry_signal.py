"""The entry signal: the pair's combined value V = mid(CE) + mid(PE) rising, window after window.

Sampled once a loop (every second). Fires when, at the same moment:
  S1/S2  V rose in each of the last ``windows`` windows of ``window_s`` seconds,
  S3     V rose at least ``min_rise_pct`` % over those windows,
  S4     NIFTY futures (spot if no future) moved at least ``min_move`` points either way over them.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from algos.strangle_scalp.settings import SignalConfig

KEEP_S = 120


@dataclass(frozen=True)
class Sample:
    ts: datetime
    ce: float
    pe: float
    fut: float

    @property
    def value(self) -> float:
        return self.ce + self.pe


@dataclass(frozen=True)
class SignalState:
    fired: bool
    ready: bool                     # enough history to judge
    windows_up: int
    rise_pct: float | None
    fut_move: float | None
    value: float | None
    driver: str | None              # the leg that gained: CE (NIFTY up) or PE (NIFTY down)
    ce_change: float | None = None
    pe_change: float | None = None

    def as_dict(self) -> dict:
        return dict(fired=self.fired, ready=self.ready, windows_up=self.windows_up,
                    rise_pct=None if self.rise_pct is None else round(self.rise_pct, 2),
                    fut_move=None if self.fut_move is None else round(self.fut_move, 2),
                    value=None if self.value is None else round(self.value, 2), driver=self.driver,
                    ce_change=self.ce_change, pe_change=self.pe_change)


class PairSignal:
    def __init__(self, cfg: SignalConfig):
        self.cfg = cfg
        self.key: tuple | None = None
        self.samples: deque[Sample] = deque()

    def reset(self, key: tuple | None = None) -> None:
        self.key = key
        self.samples.clear()

    def add(self, now: datetime, ce: float, pe: float, fut: float) -> None:
        self.samples.append(Sample(now, ce, pe, fut))
        cutoff = now - timedelta(seconds=KEEP_S)
        while self.samples and self.samples[0].ts < cutoff:
            self.samples.popleft()

    def at(self, when: datetime) -> Sample | None:
        """The latest sample at or before ``when`` (no older than one loop and a bit)."""
        times = [s.ts for s in self.samples]
        i = bisect_right(times, when) - 1
        if i < 0 or (when - self.samples[i].ts).total_seconds() > 1.5:
            return None
        return self.samples[i]

    def recent(self, seconds: float = 60) -> list[float]:
        if not self.samples:
            return []
        start = self.samples[-1].ts - timedelta(seconds=seconds)
        return [round(s.value, 2) for s in self.samples if s.ts >= start]

    def evaluate(self, now: datetime, min_move: float) -> SignalState:
        cfg = self.cfg
        last = self.samples[-1] if self.samples else None
        if last is None:
            return SignalState(False, False, 0, None, None, None, None)
        w = timedelta(seconds=cfg.window_s)
        up = 0
        for i in range(cfg.windows):
            a, b = self.at(last.ts - i * w), self.at(last.ts - (i + 1) * w)
            if a is None or b is None or a.value - b.value <= 0:
                break
            up += 1
        start = self.at(last.ts - cfg.windows * w)
        if start is None:
            return SignalState(False, False, up, None, None, last.value, None)
        rise = (last.value - start.value) / start.value * 100 if start.value else 0.0
        move = last.fut - start.fut
        ce_ch, pe_ch = round(last.ce - start.ce, 2), round(last.pe - start.pe, 2)
        driver = "CE" if ce_ch > pe_ch else "PE"
        fired = up >= cfg.windows and rise >= cfg.min_rise_pct and abs(move) >= min_move
        return SignalState(fired, True, up, rise, move, last.value, driver, ce_ch, pe_ch)
