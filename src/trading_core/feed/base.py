"""Market data feed interface. Live (Kite WebSocket) and replay (stored ticks) both implement it."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Tick:
    instrument_token: int
    ltp: float | None
    bid: float | None
    ask: float | None
    ts: datetime                      # exchange time (or snapshot time in replay)
    received_at: datetime             # when we got it; used for staleness
    bid_qty: int | None = None
    ask_qty: int | None = None
    oi: int | None = None
    volume: int | None = None

    @property
    def has_depth(self) -> bool:
        return bool(self.bid and self.ask and self.bid > 0 and self.ask > 0)

    @property
    def spread(self) -> float | None:
        return (self.ask - self.bid) if self.has_depth else None


class TickTable:
    """Thread-safe latest-tick-per-token table (the WebSocket thread writes, the algo loop reads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ticks: dict[int, Tick] = {}
        self._last_received: datetime | None = None

    def update(self, ticks: Iterable[Tick]) -> None:
        with self._lock:
            for t in ticks:
                self._ticks[t.instrument_token] = t
                if self._last_received is None or t.received_at > self._last_received:
                    self._last_received = t.received_at

    def get(self, token: int) -> Tick | None:
        with self._lock:
            return self._ticks.get(token)

    def all(self) -> list[Tick]:
        with self._lock:
            return list(self._ticks.values())

    @property
    def last_received(self) -> datetime | None:
        with self._lock:
            return self._last_received


class MarketFeed(ABC):
    def start(self) -> None:
        """Connect / load data. Safe to call more than once."""

    def stop(self) -> None:
        """Disconnect."""

    @abstractmethod
    def subscribe(self, tokens: Iterable[int]) -> None: ...

    @property
    @abstractmethod
    def subscribed(self) -> set[int]: ...

    @abstractmethod
    def latest(self, token: int) -> Tick | None: ...

    @abstractmethod
    def last_tick_age(self, now: datetime) -> float | None:
        """Seconds since any subscribed instrument last ticked; None if nothing yet."""

    def snapshot(self) -> list[Tick]:
        """Latest tick of every subscribed token (used by the tick recorder)."""
        out = []
        for tok in self.subscribed:
            t = self.latest(tok)
            if t is not None:
                out.append(t)
        return out
