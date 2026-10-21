"""Time source for the platform.

Everything asks a ``Clock`` for the time instead of calling ``datetime.now()``, so the same
algo code runs against the wall clock (live) or a simulated clock (replay, tests).
"""

from __future__ import annotations

import time as _time
from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta, timezone

# India has no DST, so a fixed offset is exact and avoids needing tzdata on Windows.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


class Clock(ABC):
    simulated: bool = False

    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...

    def today(self) -> date:
        return self.now().date()

    def at(self, t: time, d: date | None = None) -> datetime:
        return ist(d or self.today(), t)


class LiveClock(Clock):
    def now(self) -> datetime:
        return datetime.now(IST)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            _time.sleep(seconds)


class SimClock(Clock):
    """Clock that only moves when ``sleep`` is called. Used for replays and tests."""

    simulated = True

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            start = start.replace(tzinfo=IST)
        self._now = start.astimezone(IST)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._now += timedelta(seconds=seconds)

    def advance_to(self, when: datetime) -> None:
        if when > self._now:
            self._now = when
