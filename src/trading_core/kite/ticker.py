"""KiteTicker WebSocket connection feeding a ``TickTable``.

Full mode is used because paper fills and exit marks need best bid/ask (market depth).
kiteconnect reconnects automatically; on every (re)connect we re-subscribe everything.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Iterable

from trading_core.clock import IST, Clock
from trading_core.feed.base import Tick, TickTable

log = logging.getLogger(__name__)


def tick_from_kite(raw: dict[str, Any], received_at: datetime) -> Tick:
    depth = raw.get("depth") or {}
    buys, sells = depth.get("buy") or [], depth.get("sell") or []
    best_bid = buys[0] if buys and buys[0].get("price") else None
    best_ask = sells[0] if sells and sells[0].get("price") else None
    ts = raw.get("exchange_timestamp") or raw.get("last_trade_time") or received_at
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)     # Kite sends naive IST datetimes
    return Tick(
        instrument_token=int(raw["instrument_token"]),
        ltp=raw.get("last_price"),
        bid=best_bid["price"] if best_bid else None,
        ask=best_ask["price"] if best_ask else None,
        bid_qty=best_bid.get("quantity") if best_bid else None,
        ask_qty=best_ask.get("quantity") if best_ask else None,
        oi=raw.get("oi"),
        volume=raw.get("volume_traded"),
        ts=ts,
        received_at=received_at,
    )


class TickerConnection:
    def __init__(self, api_key: str, access_token: str, table: TickTable, clock: Clock):
        from kiteconnect import KiteTicker

        self.table = table
        self.clock = clock
        self._tokens: set[int] = set()
        self._lock = threading.Lock()
        self.connected = False
        self.ws = KiteTicker(api_key, access_token)
        self.ws.on_ticks = self._on_ticks
        self.ws.on_connect = self._on_connect
        self.ws.on_close = self._on_close
        self.ws.on_error = self._on_error
        self.ws.on_reconnect = lambda _ws, n: log.warning("ticker reconnecting (attempt %s)", n)
        self.ws.on_noreconnect = lambda _ws: log.error("ticker gave up reconnecting")

    def start(self) -> None:
        self.ws.connect(threaded=True)

    def stop(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass

    def subscribe(self, tokens: Iterable[int]) -> None:
        new = set(int(t) for t in tokens)
        with self._lock:
            new -= self._tokens
            self._tokens |= new
        if new and self.connected:
            self._send(list(new))

    @property
    def tokens(self) -> set[int]:
        with self._lock:
            return set(self._tokens)

    def _send(self, tokens: list[int]) -> None:
        self.ws.subscribe(tokens)
        self.ws.set_mode(self.ws.MODE_FULL, tokens)

    def _on_connect(self, _ws, _response) -> None:
        self.connected = True
        tokens = list(self.tokens)
        log.info("ticker connected, subscribing %d tokens", len(tokens))
        if tokens:
            self._send(tokens)

    def _on_ticks(self, _ws, ticks: list[dict[str, Any]]) -> None:
        now = self.clock.now()
        self.table.update(tick_from_kite(t, now) for t in ticks)

    def _on_close(self, _ws, code, reason) -> None:
        self.connected = False
        log.warning("ticker closed: %s %s", code, reason)

    def _on_error(self, _ws, code, reason) -> None:
        log.error("ticker error: %s %s", code, reason)
