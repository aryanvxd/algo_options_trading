"""Trading-day calendar. Expiries are never hard-coded: they come from the instrument dump, and
this class only answers "is this a trading day" and "how many trading days until X"."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


class MarketCalendar:
    def __init__(self, holidays: Iterable[date] = ()):
        self.holidays = set(holidays)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def trading_days_until(self, d: date, expiry: date) -> int:
        """Days to expiry in trading days: 0 on expiry day, 1 the trading day before, ...

        Raises if ``expiry`` is before ``d``.
        """
        if expiry < d:
            raise ValueError(f"expiry {expiry} is before {d}")
        n, cur = 0, d
        while cur < expiry:
            cur = self.next_trading_day(cur)
            n += 1
        return n

    def window_before(self, expiry: date, n_days: int) -> list[date]:
        """The ``n_days`` trading days ending on (and including) ``expiry``."""
        days = [expiry]
        while len(days) < n_days:
            days.insert(0, self.prev_trading_day(days[0]))
        return days
