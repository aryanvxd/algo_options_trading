"""One backtest day, rebuilt from the Parquet store (docs/design/backtest.md, section 3).

  index_1m   NIFTY 50, INDIA VIX, NIFTY FUT (continuous future, with volume)
  equity_1m  the NIFTY 50 stocks
  options_eod  that day's chain (expiries, strikes) and the previous day's (IV smile)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pyarrow.parquet as pq

from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)

INDEX, VIX = "NIFTY 50", "INDIA VIX"
TUESDAY_EXPIRY_FROM = date(2025, 9, 1)     # NIFTY weeklies moved from Thursday to Tuesday


@dataclass
class Bars:
    """One instrument's 1-minute bars for a day, as numpy arrays (minute = minutes since midnight)."""
    minutes: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @property
    def last_close(self) -> float | None:
        return float(self.close[-1]) if len(self.close) else None

    @classmethod
    def from_table(cls, t) -> "Bars | None":
        if t is None or t.num_rows == 0:
            return None
        ts = t["ts"].to_pylist()
        vol = t["volume"].to_numpy(zero_copy_only=False) if "volume" in t.column_names else np.zeros(len(ts))
        return cls(np.array([x.hour * 60 + x.minute for x in ts]),
                   *(t[c].to_numpy(zero_copy_only=False).astype(float) for c in ("open", "high", "low", "close")),
                   np.nan_to_num(vol.astype(float)))


@dataclass
class Smile:
    """IV by strike for each side, from an end-of-day chain (flat beyond the outer strikes)."""
    strikes: dict[str, np.ndarray] = field(default_factory=dict)
    ivs: dict[str, np.ndarray] = field(default_factory=dict)
    atm_iv: float | None = None

    def iv(self, strike: float, opt: str) -> float | None:
        k = self.strikes.get(opt)
        if k is not None and len(k):
            return float(np.interp(strike, k, self.ivs[opt]))
        return self.atm_iv

    @property
    def empty(self) -> bool:
        return not self.strikes and self.atm_iv is None


@dataclass
class DayData:
    day: date
    index: Bars
    vix: Bars | None
    fut: Bars | None
    stocks: dict[str, Bars]
    prev_closes: dict[str, float]
    expiry: date
    expiry_source: str                 # bhavcopy | rule
    strikes: list[float]
    smile: Smile
    vix_ref: float | None              # previous day's closing VIX (the smile's level)

    def coverage(self) -> dict[str, object]:
        return dict(vix=self.vix is not None, fut=self.fut is not None, stocks=len(self.stocks),
                    prev_closes=len(self.prev_closes), expiry=self.expiry_source, smile=not self.smile.empty)


class History:
    def __init__(self, store: MarketStore, underlying: str = "NIFTY", source: str = "kite",
                 strike_step: int = 50):
        self.store, self.underlying, self.source, self.step = store, underlying, source, strike_step

    # ------------------------------------------------------------ calendar

    def trading_days(self, start: date, end: date) -> list[date]:
        """Days with NIFTY minute data (a stored but empty day is a holiday)."""
        out = []
        for d in self.store.days("index_1m", INDEX, self.source):
            if start <= d <= end:
                path = self.store.path("index_1m", INDEX, self.source, day=d)
                if pq.read_metadata(path).num_rows:
                    out.append(d)
        return out

    # ------------------------------------------------------------ loading

    def _bars(self, dataset: str, key: str, d: date) -> Bars | None:
        return Bars.from_table(self.store.read(dataset, key, self.source, day=d))

    def _chain(self, d: date | None):
        if d is None:
            return None
        t = self.store.read("options_eod", self.underlying, "nse", day=d)
        return t if t is not None and t.num_rows else None

    def load(self, d: date, prev: date | None, symbols: list[str], trading: set[date]) -> DayData | None:
        index = self._bars("index_1m", INDEX, d)
        if index is None:
            return None
        stocks, prev_closes = {}, {}
        for s in symbols:
            b = self._bars("equity_1m", s, d)
            p = self._bars("equity_1m", s, prev) if prev else None
            if b is not None and p is not None and p.last_close:
                stocks[s], prev_closes[s] = b, p.last_close
        vix_prev = self._bars("index_1m", VIX, prev) if prev else None
        chain, prev_chain = self._chain(d), self._chain(prev)
        expiry, source = self._expiry(d, chain or prev_chain, trading)
        strikes = self._strikes(expiry, chain or prev_chain, index)
        return DayData(day=d, index=index, vix=self._bars("index_1m", VIX, d),
                       fut=self._bars("index_1m", f"{self.underlying} FUT", d), stocks=stocks, prev_closes=prev_closes,
                       expiry=expiry, expiry_source=source, strikes=strikes,
                       smile=self._smile(prev_chain, expiry, index), vix_ref=vix_prev.last_close if vix_prev else None)

    def _expiry(self, d: date, chain, trading: set[date]) -> tuple[date, str]:
        if chain is not None:
            ex = sorted({e for e in chain["expiry"].to_pylist() if e is not None and e >= d})
            if ex:
                return ex[0], "bhavcopy"
        weekday = 1 if d >= TUESDAY_EXPIRY_FROM else 3
        e = d + timedelta(days=(weekday - d.weekday()) % 7)
        last_known = max(trading) if trading else d
        while e > d and e <= last_known and e not in trading:   # a holiday: the day before
            e -= timedelta(days=1)
        return e, "rule"

    def _strikes(self, expiry: date, chain, index: Bars) -> list[float]:
        if chain is not None:
            ks = sorted({k for k, e in zip(chain["strike"].to_pylist(), chain["expiry"].to_pylist())
                         if e == expiry and k is not None})
            if ks:
                return ks
        mid = round(float(index.open[0]) / self.step) * self.step
        return [float(mid + i * self.step) for i in range(-60, 61)]

    def _smile(self, chain, expiry: date, index: Bars) -> Smile:
        sm = Smile()
        if chain is None or "iv" not in chain.column_names:
            return sm
        rows = [(k, o, iv) for k, o, e, iv in zip(chain["strike"].to_pylist(), chain["opt_type"].to_pylist(),
                                                  chain["expiry"].to_pylist(), chain["iv"].to_pylist())
                if e == expiry and iv is not None and np.isfinite(iv) and 0.01 < iv < 3]
        if not rows:            # the expiry wasn't listed yesterday (a new weekly): use the nearest other one
            others = sorted({e for e in chain["expiry"].to_pylist()
                             if e is not None and e != expiry and e >= expiry - timedelta(days=7)})
            return self._smile(chain, others[0], index) if others else sm
        spot = float(index.open[0])
        for opt in ("CE", "PE"):
            pts = sorted((k, iv) for k, o, iv in rows if o == opt)
            if pts:
                sm.strikes[opt] = np.array([p[0] for p in pts], dtype=float)
                sm.ivs[opt] = np.array([p[1] for p in pts], dtype=float)
        near = min(rows, key=lambda r: abs(r[0] - spot))
        sm.atm_iv = float(near[2])
        return sm

