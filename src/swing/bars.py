"""Daily and weekly candles for the swing scan, from the ``equity_daily`` store (spec section 2).

Kite's daily candles are adjusted for splits and bonuses. Weekly bars are built here from the
daily ones: Monday-Friday, dated by the week's last trading day.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trading_core.marketdata.store import MarketStore, conform, slug


@dataclass
class Bars:
    """One instrument's candles, oldest first."""
    symbol: str
    dates: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return len(self.dates)

    def since(self, start: date) -> Bars:
        i = next((k for k, d in enumerate(self.dates) if d >= start), len(self.dates))
        return Bars(self.symbol, self.dates[i:], self.open[i:], self.high[i:], self.low[i:],
                    self.close[i:], self.volume[i:])


def _from_rows(symbol: str, rows: list[tuple]) -> Bars:
    rows.sort(key=lambda r: r[0])
    cols = list(zip(*rows)) if rows else [[]] * 6
    f = lambda c: np.asarray(c, dtype=float)  # noqa: E731
    return Bars(symbol, list(cols[0]), f(cols[1]), f(cols[2]), f(cols[3]), f(cols[4]), f(cols[5]))


def daily_bars(store: MarketStore, universe: str, symbols: Iterable[str] | None = None,
               source: str = "kite") -> dict[str, Bars]:
    """Every stored day of each symbol in ``universe`` (all of them when ``symbols`` is None)."""
    ds = store.dataset("equity_daily")
    base = store.root / ds.name / f"{ds.key_name}={slug(universe)}"
    files = sorted(base.glob(f"year=*/part-{slug(source)}.parquet"))
    if not files:
        return {}
    table = pa.concat_tables([conform(pq.read_table(p), ds.schema) for p in files])
    if symbols is not None:
        table = table.filter(pc.is_in(table["symbol"], pa.array(list(symbols), type=pa.string())))
    by_sym: dict[str, list[tuple]] = defaultdict(list)
    cols = [table[c].to_pylist() for c in ("symbol", "trade_date", "open", "high", "low", "close", "volume")]
    for sym, *rest in zip(*cols):
        by_sym[sym].append(tuple(rest))
    return {s: _from_rows(s, rows) for s, rows in sorted(by_sym.items())}


def weekly(daily: Bars) -> Bars:
    """Monday-Friday bars: first open, highest high, lowest low, last close, summed volume,
    dated by the week's last trading day (a short holiday week is still one bar)."""
    groups: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, d in enumerate(daily.dates):
        groups[tuple(d.isocalendar())[:2]].append(i)
    rows = [(daily.dates[ix[-1]], daily.open[ix[0]], daily.high[ix].max(), daily.low[ix].min(),
             daily.close[ix[-1]], daily.volume[ix].sum()) for ix in (np.asarray(v) for v in groups.values())]
    return _from_rows(daily.symbol, rows)


def avg_traded_value_cr(bars: Bars, days: int = 20) -> float:
    """S5 liquidity: average of close x volume over the last ``days`` sessions, in ₹ crore."""
    if len(bars) == 0:
        return 0.0
    n = min(days, len(bars))
    return float((bars.close[-n:] * bars.volume[-n:]).mean() / 1e7)
