from __future__ import annotations

from datetime import datetime
from typing import Iterable

from trading_core.clock import Clock
from trading_core.feed.base import MarketFeed, Tick, TickTable
from trading_core.kite.ticker import TickerConnection


class LiveFeed(MarketFeed):
    """Kite WebSocket feed. Connection is opened lazily on ``start``."""

    def __init__(self, api_key: str, access_token: str, clock: Clock):
        self.table = TickTable()
        self.clock = clock
        self.conn = TickerConnection(api_key, access_token, self.table, clock)
        self._started = False
        self._started_at: datetime | None = None

    def start(self) -> None:
        if not self._started:
            self.conn.start()
            self._started = True
            self._started_at = self.clock.now()

    def stop(self) -> None:
        if self._started:
            self.conn.stop()
            self._started = False

    def subscribe(self, tokens: Iterable[int]) -> None:
        self.conn.subscribe(tokens)

    @property
    def subscribed(self) -> set[int]:
        return self.conn.tokens

    def latest(self, token: int) -> Tick | None:
        return self.table.get(token)

    def last_tick_age(self, now: datetime) -> float | None:
        # Until the first tick, count from the connect: a restart mid-trade gets the same grace as a
        # dropped connection instead of reading as "never ticked" and exiting at once.
        last = self.table.last_received or self._started_at
        return None if last is None else (now - last).total_seconds()
