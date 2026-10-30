"""Intraday chart pieces built from prices as they arrive: 1-minute bars, Renko bricks and VWAP.

All three are fed the same way live and in a replay (and from Kite's 1-minute candles to warm up
before the algo started), so a signal on a replay is the signal the live run saw.

Renko (classic, close-based): a brick forms when the price closes one brick size beyond the last
brick in the same direction; a brick the other way needs a move of two brick sizes (back past the
last brick's open). Several bricks can form from one big close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

UP, DOWN = 1, -1


@dataclass(frozen=True)
class Bar:
    ts: datetime              # minute start
    open: float
    high: float
    low: float
    close: float


class MinuteBars:
    """Aggregates prices into 1-minute bars; a bar is complete once its minute has passed."""

    def __init__(self, grace_s: float = 2.0):
        self.grace = timedelta(seconds=grace_s)
        self.current: Bar | None = None
        self.last_closed: datetime | None = None

    def add(self, ts: datetime, price: float) -> list[Bar]:
        start = ts.replace(second=0, microsecond=0)
        done = self._close() if self.current is not None and start > self.current.ts else self.flush(ts)
        if self.last_closed is not None and start <= self.last_closed:
            return done                         # a late price for a minute already closed
        c = self.current
        if c is None:
            self.current = Bar(start, price, price, price, price)
        else:
            self.current = Bar(c.ts, c.open, max(c.high, price), min(c.low, price), price)
        return done

    def flush(self, now: datetime) -> list[Bar]:
        """Close the current bar once its minute (plus a little grace for late ticks) is over."""
        c = self.current
        if c is not None and now >= c.ts + timedelta(minutes=1) + self.grace:
            return self._close()
        return []

    def _close(self) -> list[Bar]:
        c, self.current, self.last_closed = self.current, None, self.current.ts
        return [c]

    def seed(self, ts: datetime) -> None:
        """Mark minutes up to ``ts`` as already closed (warm-up candles covered them)."""
        self.last_closed = ts


@dataclass(frozen=True)
class Brick:
    ts: datetime              # when it formed (the close that completed it)
    direction: int            # UP / DOWN
    open: float
    close: float


class Renko:
    def __init__(self, size: float):
        self.size = float(size)
        self.anchor: float | None = None
        self.bricks: list[Brick] = []

    @property
    def last(self) -> Brick | None:
        return self.bricks[-1] if self.bricks else None

    def add(self, ts: datetime, price: float) -> list[Brick]:
        """Feed a close; returns the bricks it completed (oldest first)."""
        s, new = self.size, []
        if self.anchor is None:
            self.anchor = price
            return new
        last = self.last
        if last is None:
            base = self.anchor
            if price >= base + s:
                last = self._brick(new, ts, UP, base)
            elif price <= base - s:
                last = self._brick(new, ts, DOWN, base)
            else:
                return new
        # continuation, then (only if nothing continued) a reversal past the last brick's open
        while True:
            if last.direction == UP and price >= last.close + s:
                last = self._brick(new, ts, UP, last.close)
            elif last.direction == DOWN and price <= last.close - s:
                last = self._brick(new, ts, DOWN, last.close)
            elif last.direction == UP and price <= last.open - s:
                last = self._brick(new, ts, DOWN, last.open)
            elif last.direction == DOWN and price >= last.open + s:
                last = self._brick(new, ts, UP, last.open)
            else:
                return new

    def _brick(self, out: list[Brick], ts: datetime, direction: int, start: float) -> Brick:
        b = Brick(ts, direction, start, start + direction * self.size)
        self.bricks.append(b)
        out.append(b)
        return b

    def run(self, direction: int, after: datetime | None = None) -> int:
        """Bricks in ``direction`` at the end of the chart (formed after ``after``, if given)."""
        n = 0
        for b in reversed(self.bricks):
            if b.direction != direction or (after is not None and b.ts <= after):
                break
            n += 1
        return n

    @property
    def reversal_level(self) -> float | None:
        """Close that would print the first brick against the current direction."""
        b = self.last
        if b is None:
            return None
        return b.open - self.size if b.direction == UP else b.open + self.size

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        return [dict(ts=b.ts.strftime("%H:%M"), dir="up" if b.direction == UP else "down", open=b.open,
                     close=b.close) for b in self.bricks[-n:]]


class Vwap:
    """Volume-weighted average price since the open.

    Warm-up adds whole 1-minute candles (typical price × volume). Live ticks carry the day's
    cumulative volume, so each tick adds its price × the volume traded since the previous tick; the
    first live tick only sets that baseline (its volume is already in the candles)."""

    def __init__(self) -> None:
        self.pv = 0.0
        self.volume = 0.0
        self._last_cum: int | None = None

    def add_bar(self, high: float, low: float, close: float, volume: int | None) -> None:
        if volume:
            self.pv += (high + low + close) / 3 * volume
            self.volume += volume

    def add_tick(self, price: float | None, cum_volume: int | None) -> None:
        if price is None or cum_volume is None:
            return
        if self._last_cum is not None and cum_volume > self._last_cum:
            v = cum_volume - self._last_cum
            self.pv += price * v
            self.volume += v
        if self._last_cum is None or cum_volume >= self._last_cum:
            self._last_cum = cum_volume

    @property
    def value(self) -> float | None:
        return round(self.pv / self.volume, 2) if self.volume else None
