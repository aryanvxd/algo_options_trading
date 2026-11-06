"""Balanced-premium short strikes for the iron condor.

    same-width shorts   short_pe0 / short_ce0 as the original (ATM ± width/2, nearest listed)
    target premium      t = (mid(short_pe0) + mid(short_ce0)) / 2
    balanced shorts     the listed PE below spot / CE above spot whose mid is closest to t,
                        at least ``min_distance`` from spot and at most ``max_shift`` from the
                        same-width strike (ties go further OTM); a side with no such strike keeps
                        its same-width strike
    hedges              ``hedge`` beyond each short, nearest listed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from algos.iron_condor.filters import mid
from algos.iron_condor.strike_selector import StrikePlan, plan_strikes
from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentInfo
from trading_core.market.options import nearest_listed


@dataclass(frozen=True)
class BalancedPick:
    plan: StrikePlan
    width_pe: float           # the same-width shorts the pick started from
    width_ce: float
    target: float             # premium both shorts aim for
    pe_mid: float
    ce_mid: float


def _closest(target: float, candidates: list[tuple[float, float]], otm_sign: int) -> float | None:
    """Strike whose premium is closest to ``target``; ties go further OTM (``otm_sign`` -1 puts, +1 calls)."""
    if not candidates:
        return None
    return min(candidates, key=lambda kp: (abs(kp[1] - target), -otm_sign * kp[0]))[0]


def pick_balanced(spot: float, width: int, hedge: int, step: int, chain: list[InstrumentInfo],
                  quote: Callable[[int], Tick | None], min_distance: float,
                  max_shift: float) -> BalancedPick | None:
    """None until the same-width shorts have prices (or the strikes can't be placed)."""
    by_key = {(i.strike, i.instrument_type): i for i in chain if i.strike is not None}
    pe = sorted(k for k, t in by_key if t == "PE")
    ce = sorted(k for k, t in by_key if t == "CE")
    base = plan_strikes(spot, width, hedge, step, pe, ce)
    if base is None:
        return None
    pe0, ce0 = mid(quote(by_key[(base.short_pe, "PE")].instrument_token)), \
        mid(quote(by_key[(base.short_ce, "CE")].instrument_token))
    if not pe0 or not ce0:
        return None
    target = (pe0 + ce0) / 2

    def side(opt: str, strikes: list[float], same_width: float, ok: Callable[[float], bool], sign: int):
        cands = []
        for k in strikes:
            if not ok(k) or abs(k - same_width) > max_shift:
                continue
            p = mid(quote(by_key[(k, opt)].instrument_token))
            if p:
                cands.append((k, p))
        k = _closest(target, cands, sign) or same_width
        return k, dict(cands).get(k) or (pe0 if opt == "PE" else ce0)

    short_pe, pe_mid = side("PE", pe, base.short_pe, lambda k: spot - k >= min_distance, -1)
    short_ce, ce_mid = side("CE", ce, base.short_ce, lambda k: k - spot >= min_distance, +1)
    long_pe = nearest_listed(short_pe - hedge, pe, tie="down")
    long_ce = nearest_listed(short_ce + hedge, ce, tie="up")
    if long_pe is None or long_ce is None or not (long_pe < short_pe < short_ce < long_ce):
        return None
    plan = StrikePlan(base.atm, width, hedge, short_pe, short_ce, long_pe, long_ce)
    return BalancedPick(plan, base.short_pe, base.short_ce, round(target, 2), round(pe_mid, 2), round(ce_mid, 2))
