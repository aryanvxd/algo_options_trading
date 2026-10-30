"""Live market breadth: advances/declines of the NIFTY 50 stocks, plain and index-weighted.

Each stock's move is its last price against the previous session's close. The previous closes come
from one Kite quote call when the algo starts (``prev_closes_from_quotes``) and are stored for the
day (``prev_closes`` table), so a replay of that day reads the same numbers.

The weighted figure is the index weight of the stocks up minus the weight of those down, as a %
of the weight that has a price (−100 = everything down, +100 = everything up). It says more about
NIFTY's direction than the plain count: HDFCBANK, ICICIBANK and RELIANCE alone are a quarter of
the index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from trading_core.config import PlatformConfig, read_yaml
from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentInfo

log = logging.getLogger(__name__)

Quote = Callable[[int], Tick | None]
FLAT_PCT = 0.01            # a move smaller than this (%) counts as unchanged


@dataclass(frozen=True)
class Member:
    token: int
    symbol: str
    weight: float          # % of the index (all members sum to 100)
    prev_close: float


@dataclass(frozen=True)
class BreadthReading:
    advances: int
    declines: int
    unchanged: int
    weighted: float                     # −100 .. +100
    coverage: float                     # % of the index weight that had a price
    leaders: list[tuple[str, float]]    # biggest weights moving, with their % change

    @property
    def seen(self) -> int:
        return self.advances + self.declines + self.unchanged

    def as_dict(self) -> dict[str, Any]:
        return dict(advances=self.advances, declines=self.declines, unchanged=self.unchanged,
                    weighted=self.weighted, coverage=self.coverage,
                    leaders=[f"{s} {p:+.2f}%" for s, p in self.leaders])


def load_weights(platform: PlatformConfig, universe: str = "nifty50") -> dict[str, float]:
    """Index weights from ``config/universes/<universe>_weights.yaml``, scaled to sum to 100."""
    path = platform.config_dir / "universes" / f"{universe}_weights.yaml"
    raw = {str(k): float(v) for k, v in (read_yaml(path).get("weights") or {}).items()} if path.exists() else {}
    total = sum(raw.values())
    return {k: v * 100 / total for k, v in raw.items()} if total else {}


def members(stocks: dict[str, InstrumentInfo], weights: dict[str, float],
            prev_closes: dict[int, float]) -> list[Member]:
    """Stocks that have a token, a previous close and a weight (equal weights if none are known)."""
    have = [(sym, inst) for sym, inst in stocks.items() if prev_closes.get(inst.instrument_token)]
    if not have:
        return []
    known = sum(weights.get(sym, 0.0) for sym, _ in have)
    out = []
    for sym, inst in have:
        w = weights.get(sym, 0.0) * 100 / known if known else 100 / len(have)
        out.append(Member(inst.instrument_token, sym, w, prev_closes[inst.instrument_token]))
    missing = set(stocks) - {m.symbol for m in out}
    if missing:
        log.warning("breadth: %d stock(s) without a previous close or token: %s", len(missing),
                    ", ".join(sorted(missing)[:10]))
    return out


def prev_closes_from_quotes(quotes: dict[str, dict[str, Any]], stocks: dict[str, InstrumentInfo],
                            exchange: str = "NSE") -> dict[int, tuple[str, float]]:
    """{token: (symbol, previous close)} from a Kite ``quote`` result (``ohlc.close``)."""
    out = {}
    for sym, inst in stocks.items():
        close = ((quotes.get(f"{exchange}:{sym}") or {}).get("ohlc") or {}).get("close")
        if close:
            out[inst.instrument_token] = (sym, float(close))
    return out


class Breadth:
    def __init__(self, stocks: Iterable[Member], min_coverage: float = 60.0, top_n: int = 5):
        self.members = list(stocks)
        self.min_coverage = min_coverage
        self.top_n = top_n

    def read(self, quote: Quote) -> BreadthReading | None:
        """None until stocks worth ``min_coverage`` % of the index have a price."""
        adv = dec = unch = 0
        up = down = seen_w = 0.0
        moves: list[tuple[str, float, float]] = []
        for m in self.members:
            t = quote(m.token)
            if t is None or not t.ltp:
                continue
            pct = (t.ltp - m.prev_close) / m.prev_close * 100
            seen_w += m.weight
            if pct > FLAT_PCT:
                adv, up = adv + 1, up + m.weight
            elif pct < -FLAT_PCT:
                dec, down = dec + 1, down + m.weight
            else:
                unch += 1
            moves.append((m.symbol, pct, m.weight))
        if not seen_w or seen_w < self.min_coverage:
            return None
        moves.sort(key=lambda x: -x[2])
        return BreadthReading(adv, dec, unch, round((up - down) * 100 / seen_w, 1), round(seen_w, 1),
                              [(s, round(p, 2)) for s, p, _ in moves[:self.top_n]])
