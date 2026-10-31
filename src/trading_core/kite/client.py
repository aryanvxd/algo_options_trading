"""Thin KiteConnect wrapper: today's token from the DB, a client-side throttle and retries."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from trading_core.clock import Clock
from trading_core.config import KiteConfig, KiteSecrets
from trading_core.db.engine import Database
from trading_core.kite.auth import NotLoggedIn, load_active_session

log = logging.getLogger(__name__)

QUOTE_BATCH = 500   # Kite's max instruments per quote call


class KiteClient:
    def __init__(self, api_key: str, access_token: str, cfg: KiteConfig):
        from kiteconnect import KiteConnect

        self.api_key = api_key
        self.access_token = access_token
        self.cfg = cfg
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._lock = threading.Lock()
        self._last_call = 0.0

    @classmethod
    def from_db(cls, db: Database, cfg: KiteConfig, secrets: KiteSecrets, clock: Clock) -> "KiteClient":
        session = load_active_session(db, clock.now())
        if session is None:
            raise NotLoggedIn("No valid Kite session for today. Run `algo login` first.")
        return cls(secrets.api_key, session.access_token, cfg)

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from kiteconnect import exceptions as kex

        for attempt in range(1, self.cfg.max_retries + 1):
            with self._lock:
                wait = self.cfg.min_request_interval_s - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
            try:
                return fn(*args, **kwargs)
            except kex.TokenException as e:
                raise NotLoggedIn(f"Kite token rejected ({e}). Run `algo login` again.") from e
            except (kex.NetworkException, kex.GeneralException) as e:
                if attempt == self.cfg.max_retries:
                    raise
                log.warning("Kite call %s failed (%s), retry %d", getattr(fn, "__name__", fn), e, attempt)
                time.sleep(0.5 * attempt)

    def quote(self, keys: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for i in range(0, len(keys), QUOTE_BATCH):
            out.update(self._call(self.kite.quote, keys[i:i + QUOTE_BATCH]) or {})
        return out

    def ltp(self, keys: list[str]) -> dict[str, Any]:
        return self._call(self.kite.ltp, keys) or {}

    def instruments(self, exchange: str) -> list[dict[str, Any]]:
        return self._call(self.kite.instruments, exchange)

    def historical(self, token: int, start: Any, end: Any, interval: str, oi: bool = False,
                   continuous: bool = False) -> list[dict[str, Any]]:
        """Candles for a live instrument (Kite has no expired options). Max range per call:
        60 days for minute candles, 2000 days for daily. ``continuous`` stitches a future's
        expired months (pass the current contract's token)."""
        return self._call(self.kite.historical_data, token, start, end, interval, continuous, oi) or []

    def margins(self) -> dict[str, Any]:
        return self._call(self.kite.margins)

    def basket_margin(self, orders: list[dict[str, Any]]) -> float | None:
        """Margin Kite would block for these orders together (hedge benefit included), ignoring the
        account's real positions. Read-only: nothing is placed."""
        res = self._call(self.kite.basket_order_margins, orders, False) or {}
        total = (res.get("final") or {}).get("total")
        return float(total) if total else None
