"""Pre-open market snapshot: India VIX, index gap and constituent breadth.

Gainers/losers come from Kite quotes of the index constituents (ranked locally) rather than
scraping nseindia.com, which blocks scripted access.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from trading_core.clock import Clock

log = logging.getLogger(__name__)


@dataclass
class PremarketSnapshot:
    captured_at: datetime
    index_symbol: str
    index_spot: float | None
    prev_close: float | None
    gap_pct: float | None
    vix: float | None
    advances: int = 0
    declines: int = 0
    unchanged: int = 0
    gainers: list[dict[str, Any]] = field(default_factory=list)
    losers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def constituents_seen(self) -> int:
        return self.advances + self.declines + self.unchanged


def _pct_change(q: dict[str, Any]) -> float | None:
    last, close = q.get("last_price"), (q.get("ohlc") or {}).get("close")
    if not last or not close:
        return None
    return (last - close) / close * 100.0


def build_snapshot(quotes: dict[str, dict[str, Any]], index_key: str, vix_key: str,
                   constituent_keys: list[str], now: datetime, top_n: int = 5) -> PremarketSnapshot:
    idx = quotes.get(index_key) or {}
    vix = (quotes.get(vix_key) or {}).get("last_price")
    spot = idx.get("last_price")
    prev_close = (idx.get("ohlc") or {}).get("close")
    gap = _pct_change(idx)

    moves: list[tuple[str, float]] = []
    for key in constituent_keys:
        pct = _pct_change(quotes.get(key) or {})
        if pct is not None:
            moves.append((key.split(":", 1)[-1], pct))
    missing = len(constituent_keys) - len(moves)
    if missing:
        log.info("premarket: %d of %d constituents had no usable quote", missing, len(constituent_keys))

    adv = sum(1 for _, p in moves if p > 0)
    dec = sum(1 for _, p in moves if p < 0)
    moves.sort(key=lambda x: x[1], reverse=True)
    gainers = [{"symbol": s, "pct": round(p, 2)} for s, p in moves[:top_n] if p > 0]
    losers = [{"symbol": s, "pct": round(p, 2)} for s, p in reversed(moves[-top_n:]) if p < 0]
    return PremarketSnapshot(captured_at=now, index_symbol=index_key.split(":", 1)[-1], index_spot=spot,
                             prev_close=prev_close, gap_pct=round(gap, 3) if gap is not None else None,
                             vix=vix, advances=adv, declines=dec, unchanged=len(moves) - adv - dec,
                             gainers=gainers, losers=losers)


class PremarketSource(ABC):
    @abstractmethod
    def capture(self, index_key: str, vix_key: str, constituent_keys: list[str]) -> PremarketSnapshot | None: ...


class KitePremarketSource(PremarketSource):
    def __init__(self, client: Any, clock: Clock):
        self.client = client
        self.clock = clock

    def capture(self, index_key: str, vix_key: str, constituent_keys: list[str]) -> PremarketSnapshot | None:
        quotes = self.client.quote([index_key, vix_key, *constituent_keys])
        return build_snapshot(quotes, index_key, vix_key, constituent_keys, self.clock.now())


class StoredPremarketSource(PremarketSource):
    """Replays: reuse the pre-open snapshot a live run saved on that date."""

    def __init__(self, journal: Any, trade_date: date):
        self.journal = journal
        self.trade_date = trade_date

    def capture(self, index_key: str, vix_key: str, constituent_keys: list[str]) -> PremarketSnapshot | None:
        row = self.journal.latest_breadth(self.trade_date)
        if row is None:
            return None
        return PremarketSnapshot(captured_at=row.captured_at, index_symbol=row.index_symbol,
                                 index_spot=row.index_spot, prev_close=row.prev_close, gap_pct=row.gap_pct,
                                 vix=row.vix, advances=row.advances, declines=row.declines,
                                 unchanged=row.unchanged, gainers=row.gainers or [], losers=row.losers or [])
