"""Strike selection for the short iron condor.

    atm      = spot rounded to the strike step
    short_pe = atm - width/2      short_ce = atm + width/2     (nearest listed, ties go further OTM)
    long_pe  = short_pe - hedge   long_ce  = short_ce + hedge
Reject if any leg has no book, a spread wider than ``max_spread_pts``, or the executable credit
(shorts at bid, longs at ask) is below ``min_credit_pts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading_core.broker.base import BUY, SELL
from trading_core.feed.base import Tick
from trading_core.kite.instruments import InstrumentInfo
from trading_core.market.options import atm_strike, nearest_listed
from trading_core.strategy.position import Leg


@dataclass(frozen=True)
class StrikePlan:
    atm: float
    width: int
    hedge: int
    short_pe: float
    short_ce: float
    long_pe: float
    long_ce: float


@dataclass
class Selection:
    plan: StrikePlan
    legs: list[Leg]
    net_credit_pts: float


@dataclass
class Rejection:
    reason: str


def plan_strikes(spot: float, width: int, hedge: int, step: int,
                 pe_strikes: list[float], ce_strikes: list[float]) -> StrikePlan | None:
    atm = atm_strike(spot, step)
    short_pe = nearest_listed(atm - width / 2, pe_strikes, tie="down")
    short_ce = nearest_listed(atm + width / 2, ce_strikes, tie="up")
    if short_pe is None or short_ce is None:
        return None
    long_pe = nearest_listed(short_pe - hedge, pe_strikes, tie="down")
    long_ce = nearest_listed(short_ce + hedge, ce_strikes, tie="up")
    if long_pe is None or long_ce is None or not (long_pe < short_pe < short_ce < long_ce):
        return None
    return StrikePlan(atm, width, hedge, short_pe, short_ce, long_pe, long_ce)


def select_condor(spot: float, width: int, hedge: int, step: int, chain: list[InstrumentInfo],
                  quote: Callable[[int], Tick | None], qty: int, max_spread_pts: float,
                  min_credit_pts: float) -> Selection | Rejection:
    by_key = {(i.strike, i.instrument_type): i for i in chain}
    pe = sorted(k for k, t in by_key if t == "PE")
    ce = sorted(k for k, t in by_key if t == "CE")
    plan = plan_strikes(spot, width, hedge, step, pe, ce)
    if plan is None:
        return Rejection("could not place all four strikes on the listed chain")

    specs = [("LONG_PE", "PE", BUY, plan.long_pe), ("LONG_CE", "CE", BUY, plan.long_ce),
             ("SHORT_PE", "PE", SELL, plan.short_pe), ("SHORT_CE", "CE", SELL, plan.short_ce)]
    legs: list[Leg] = []
    credit = 0.0
    for role, opt, side, strike in specs:
        inst = by_key[(strike, opt)]
        t = quote(inst.instrument_token)
        if t is None or not t.has_depth:
            return Rejection(f"{role} {inst.tradingsymbol}: no bid/ask yet")
        if t.spread > max_spread_pts:
            return Rejection(f"{role} {inst.tradingsymbol}: spread {t.spread:.2f} > {max_spread_pts}")
        credit += t.bid if side == SELL else -t.ask
        legs.append(Leg(role=role, instrument_token=inst.instrument_token, tradingsymbol=inst.tradingsymbol,
                        side=side, qty=qty, exchange=inst.exchange, option_type=opt, strike=strike))
    credit = round(credit, 2)
    if credit < min_credit_pts:
        return Rejection(f"net credit {credit:.2f} < min {min_credit_pts}")
    return Selection(plan=plan, legs=legs, net_credit_pts=credit)
