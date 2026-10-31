"""BacktestFeed: a market day rebuilt from 1-minute bars, served through the normal feed interface.

Bar instruments (index, VIX, future, stocks) tick 4 times a minute, 15 s apart, along the usual
OHLC path: open, the extreme nearer the open, the other extreme, close. Options are priced on
demand with Black-76 (docs/design/backtest.md, section 3).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from trading_core.backtest.history import Bars, DayData
from trading_core.clock import Clock
from trading_core.feed.base import MarketFeed, Tick
from trading_core.marketdata import greeks

SEGMENT_S = 15


@dataclass(frozen=True)
class OptionSpec:
    strike: float
    opt: str            # CE | PE
    expiry: date


def _round05(x: float) -> float:
    return round(round(x * 20) / 20, 2)


def _path(b: Bars, i: int, wicks: bool = True) -> tuple[float, float, float, float]:
    o, h, l, c = b.open[i], b.high[i], b.low[i], b.close[i]
    if not wicks:
        return (o, o, c, c)
    return (o, l, h, c) if c >= o else (o, h, l, c)


class BacktestFeed(MarketFeed):
    def __init__(self, data: DayData, clock: Clock, bars: dict[int, Bars], options: dict[int, OptionSpec],
                 index_token: int, vix_token: int | None, r: float, spread_frac: float = 0.001,
                 no_wicks: Iterable[int] = ()):
        self.data, self.clock, self.r, self.spread_frac = data, clock, r, spread_frac
        self.bars, self.options = bars, options
        self.index_token, self.vix_token = index_token, vix_token
        # Stocks move open -> close only: on their shared 15 s grid every stock would otherwise touch
        # its low and high at the same moment, a lock-step wick real stocks never make.
        self.no_wicks = set(no_wicks)
        self._cumvol = {tok: _cumsum(b.volume) for tok, b in bars.items()}
        self._sub: set[int] = set()
        self._cache: dict[int, Tick] = {}
        self._cache_key: tuple | None = None

    # ------------------------------------------------------------ MarketFeed

    def subscribe(self, tokens: Iterable[int]) -> None:
        self._sub |= set(int(t) for t in tokens)

    @property
    def subscribed(self) -> set[int]:
        return set(self._sub)

    def latest(self, token: int) -> Tick | None:
        now = self.clock.now()
        key = (now.hour, now.minute, now.second // SEGMENT_S)
        if key != self._cache_key:
            self._cache, self._cache_key = {}, key
        if token in self._cache:
            return self._cache[token]
        t = self._bar_tick(token, now) if token in self.bars else self._option_tick(token, now)
        self._cache[token] = t
        return t

    def last_tick_age(self, now: datetime) -> float | None:
        b = self.bars[self.index_token]
        m = now.hour * 60 + now.minute
        i = bisect_right(b.minutes, m) - 1
        if i < 0:
            return None
        if b.minutes[i] == m:
            return 0.5
        end = now.replace(hour=int(b.minutes[i]) // 60, minute=int(b.minutes[i]) % 60, second=0, microsecond=0) \
            + timedelta(minutes=1)
        return max(0.5, (now - end).total_seconds())      # a gap in the data reads as a stale feed

    # ------------------------------------------------------------ prices

    def _bar_tick(self, token: int, now: datetime) -> Tick | None:
        b = self.bars[token]
        m = now.hour * 60 + now.minute
        i = bisect_right(b.minutes, m) - 1
        if i < 0:
            return None
        cum = self._cumvol[token]
        if b.minutes[i] == m:
            k = min(3, now.second // SEGMENT_S)
            price = _path(b, i, token not in self.no_wicks)[k]
            vol = (cum[i - 1] if i else 0.0) + b.volume[i] * (k + 1) / 4
            ts = now.replace(second=k * SEGMENT_S, microsecond=0)
        else:                       # no bar this minute: the last close stands
            price, vol = b.close[i], cum[i]
            ts = now.replace(second=0, microsecond=0)
        return Tick(token, ltp=float(price), bid=None, ask=None, ts=ts, received_at=now,
                    volume=int(vol) if vol else None)

    def _last_close(self, token: int | None, now: datetime) -> float | None:
        """Close of the last completed minute: IV follows VIX's minute closes, not its intra-minute
        wiggle (a 1-point VIX swing would move an ATM option by several points)."""
        b = self.bars.get(token) if token is not None else None
        return _last_close_of(b, now.hour * 60 + now.minute) if b is not None else None

    def _option_tick(self, token: int, now: datetime) -> Tick | None:
        spec = self.options.get(token)
        spot_t = self._bar_tick(self.index_token, now) if spec else None
        if spec is None or spot_t is None:
            return None
        sigma = self.data.smile.iv(spec.strike, spec.opt)
        vix = self._last_close(self.vix_token, now)
        if sigma is None:
            sigma = vix / 100 if vix else 0.13
        elif vix and self.data.vix_ref:
            sigma *= vix / self.data.vix_ref
        t = greeks.years_between(now, spec.expiry)
        f = spot_t.ltp * math.exp(self.r * t)
        p = float(greeks.price(f, spec.strike, t, self.r, sigma, spec.opt == "CE"))
        intrinsic = max(0.0, spot_t.ltp - spec.strike) if spec.opt == "CE" else max(0.0, spec.strike - spot_t.ltp)
        mid = _round05(max(p, intrinsic, 0.05))
        half = max(0.05, _round05(self.spread_frac * mid))
        bid = max(0.05, _round05(mid - half))
        return Tick(token, ltp=mid, bid=bid, ask=_round05(mid + half), ts=spot_t.ts, received_at=now,
                    bid_qty=1000, ask_qty=1000)


def _last_close_of(b: Bars, minute: int) -> float | None:
    i = bisect_right(b.minutes, minute - 1) - 1
    return float(b.close[i]) if i >= 0 else (float(b.open[0]) if len(b.open) else None)


def _cumsum(v) -> list[float]:
    out, s = [], 0.0
    for x in v:
        s += float(x)
        out.append(s)
    return out
