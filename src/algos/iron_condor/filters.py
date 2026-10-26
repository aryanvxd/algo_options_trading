"""Entry-time market checks: CE/PE premium balance and PCR.

Premium balance compares a call and a put the same distance from *spot* (not the rounded ATM, which
would make one side look cheaper just from rounding). Prices at the exact distance are linearly
interpolated between the two neighbouring listed strikes, using mid prices.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Callable

from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentInfo

Quote = Callable[[int], Tick | None]


@dataclass(frozen=True)
class PremiumBalance:
    distance: float
    ce: float
    pe: float

    @property
    def ratio(self) -> float:
        return max(self.ce, self.pe) / min(self.ce, self.pe)

    @property
    def richer(self) -> str:
        return "CE" if self.ce > self.pe else "PE" if self.pe > self.ce else "equal"


def mid(t: Tick | None) -> float | None:
    if t is None:
        return None
    if t.has_depth:
        return (t.bid + t.ask) / 2
    return t.ltp or None


def price_at(target: float, opt: str, chain: list[InstrumentInfo], quote: Quote) -> float | None:
    """Mid price of ``opt`` at strike ``target``, interpolated between the listed strikes around it."""
    by_strike = {i.strike: i for i in chain if i.instrument_type == opt and i.strike is not None}
    strikes = sorted(by_strike)
    i = bisect_left(strikes, target)
    if i < len(strikes) and strikes[i] == target:
        return mid(quote(by_strike[target].instrument_token))
    if i == 0 or i == len(strikes):
        return None
    lo, hi = strikes[i - 1], strikes[i]
    p_lo, p_hi = mid(quote(by_strike[lo].instrument_token)), mid(quote(by_strike[hi].instrument_token))
    if p_lo is None or p_hi is None:
        return None
    return p_lo + (p_hi - p_lo) * (target - lo) / (hi - lo)


def premium_balance(spot: float, distance: float, chain: list[InstrumentInfo],
                    quote: Quote) -> PremiumBalance | None:
    """CE at ``spot + distance`` vs PE at ``spot - distance``; None until both have prices."""
    ce = price_at(spot + distance, "CE", chain, quote)
    pe = price_at(spot - distance, "PE", chain, quote)
    if not ce or not pe or ce <= 0 or pe <= 0:
        return None
    return PremiumBalance(distance, round(ce, 2), round(pe, 2))


def pcr(atm: float, range_pts: float, chain: list[InstrumentInfo], quote: Quote) -> float | None:
    """Put-call ratio of open interest over strikes within ``atm ± range_pts``."""
    oi = {"CE": 0, "PE": 0}
    for i in chain:
        if i.strike is None or abs(i.strike - atm) > range_pts or i.instrument_type not in oi:
            continue
        t = quote(i.instrument_token)
        if t is not None and t.oi:
            oi[i.instrument_type] += t.oi
    if not oi["CE"] or not oi["PE"]:
        return None
    return round(oi["PE"] / oi["CE"], 3)
