"""Replays tick snapshots recorded by live runs, driven by a simulated clock.

``latest(token)`` returns the last snapshot at or before ``clock.now()``, so the algo sees the
day exactly as the recorder stored it (every few seconds).
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import select

from trading_core.clock import IST, Clock
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.feed.base import MarketFeed, Tick


class ReplayFeed(MarketFeed):
    def __init__(self, db: Database, trade_date: date, clock: Clock, run_ids: list[int] | None = None,
                 store=None):
        self.db = db
        self.trade_date = trade_date
        self.clock = clock
        self.run_ids = run_ids
        self.store = store            # MarketStore: fallback once SQLite ticks are pruned
        self._times: dict[int, list[float]] = {}
        self._ticks: dict[int, list[Tick]] = {}
        self._subscribed: set[int] = set()
        self._all_times: list[float] = []
        self._loaded = False

    def start(self) -> None:
        if self._loaded:
            return
        q = (select(m.TickSnapshot).join(m.AlgoRun, m.AlgoRun.run_id == m.TickSnapshot.run_id)
             .where(m.AlgoRun.trade_date == self.trade_date, m.AlgoRun.source == "live")
             .order_by(m.TickSnapshot.ts))
        if self.run_ids:
            q = q.where(m.TickSnapshot.run_id.in_(self.run_ids))
        with self.db.session() as s:
            for r in s.execute(q).scalars():
                self._add(Tick(instrument_token=r.instrument_token, ltp=r.ltp, bid=r.bid, ask=r.ask, ts=r.ts,
                               received_at=r.ts, bid_qty=r.bid_qty, ask_qty=r.ask_qty, oi=r.oi, volume=r.volume))
        if not self._ticks and self.store is not None:
            self._load_parquet()
        for tok in self._ticks:
            order = sorted(range(len(self._times[tok])), key=self._times[tok].__getitem__)
            self._times[tok] = [self._times[tok][i] for i in order]
            self._ticks[tok] = [self._ticks[tok][i] for i in order]
        self._all_times = sorted({ts for times in self._times.values() for ts in times})
        self._loaded = True

    def _add(self, t: Tick) -> None:
        self._times.setdefault(t.instrument_token, []).append(t.ts.timestamp())
        self._ticks.setdefault(t.instrument_token, []).append(t)

    def _load_parquet(self) -> None:
        from trading_core.marketdata.sources.recorder import read_ticks

        for table in read_ticks(self.store, self.trade_date):
            cols = table.to_pydict()
            none = [None] * table.num_rows      # index ticks have no book columns
            for ts, tok, ltp, bid, ask, bq, aq, oi, vol in zip(
                    cols["ts"], cols["instrument_token"], cols["ltp"], cols.get("bid", none), cols.get("ask", none),
                    cols.get("bid_qty", none), cols.get("ask_qty", none), cols.get("oi", none),
                    cols.get("volume", none)):
                ts = ts.replace(tzinfo=IST)
                self._add(Tick(instrument_token=tok, ltp=ltp, bid=bid, ask=ask, ts=ts, received_at=ts,
                               bid_qty=bq, ask_qty=aq, oi=oi, volume=vol))

    @property
    def loaded_tokens(self) -> set[int]:
        return set(self._ticks)

    @property
    def tick_count(self) -> int:
        return sum(len(v) for v in self._ticks.values())

    def subscribe(self, tokens: Iterable[int]) -> None:
        self._subscribed |= set(int(t) for t in tokens)

    @property
    def subscribed(self) -> set[int]:
        return set(self._subscribed)

    def latest(self, token: int) -> Tick | None:
        times = self._times.get(token)
        if not times:
            return None
        i = bisect_right(times, self.clock.now().timestamp()) - 1
        return self._ticks[token][i] if i >= 0 else None

    def last_tick_age(self, now: datetime) -> float | None:
        # Age of the most recent recorded snapshot of any instrument: the recorder stores the
        # whole subscribed set at each snapshot, so this mirrors "is the feed alive".
        i = bisect_right(self._all_times, now.timestamp()) - 1
        return None if i < 0 else now.timestamp() - self._all_times[i]
