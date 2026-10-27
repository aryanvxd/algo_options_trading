"""Payoff of a multi-leg option position at expiry, in rupees.

Used to estimate the capital a defined-risk position can lose (when the broker's margin is not
available) and by the dashboard's payoff chart. Only legs with an entry price count.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from trading_core.broker.base import BUY
from trading_core.strategy.position import Leg


def _intrinsic(option_type: str | None, strike: float, spot: float) -> float:
    if option_type == "CE":
        return max(0.0, spot - strike)
    if option_type == "PE":
        return max(0.0, strike - spot)
    return spot - strike            # future: strike = entry price is handled by the caller


def expiry_pnl(legs: Iterable[Leg], spot: float) -> float:
    """Gross P&L in rupees if every leg is held to expiry and NIFTY settles at ``spot``."""
    total = 0.0
    for leg in legs:
        if leg.entry_price is None or leg.strike is None:
            continue
        sign = 1 if leg.side == BUY else -1
        total += sign * (_intrinsic(leg.option_type, leg.strike, spot) - leg.entry_price) * leg.qty
    return total


def curve(legs: Sequence[Leg], spots: Sequence[float]) -> list[float]:
    return [round(expiry_pnl(legs, s), 2) for s in spots]


def breakevens(legs: Sequence[Leg], spots: Sequence[float]) -> list[float]:
    """Spots where the expiry P&L crosses zero (linear interpolation between grid points)."""
    out: list[float] = []
    values = [expiry_pnl(legs, s) for s in spots]
    for (s0, v0), (s1, v1) in zip(zip(spots, values), zip(spots[1:], values[1:])):
        if v0 == 0:
            out.append(round(s0, 2))
        elif v0 * v1 < 0:
            out.append(round(s0 + (s1 - s0) * (-v0) / (v1 - v0), 2))
    return out


def max_loss(legs: Sequence[Leg]) -> float | None:
    """Worst expiry loss in rupees (a positive number), or None if the loss is unlimited.

    Option payoffs are piecewise linear with kinks at the strikes, so the worst case is at a
    strike or at one of the two ends (spot 0, spot very high)."""
    priced = [l for l in legs if l.entry_price is not None and l.strike is not None]
    if not priced:
        return None
    net_calls = sum((1 if l.side == BUY else -1) * l.qty for l in priced if l.option_type == "CE")
    if net_calls < 0:
        return None                 # more calls sold than bought: loss grows without limit
    strikes = sorted({l.strike for l in priced})
    points = [0.0, *strikes, strikes[-1] * 2]
    worst = min(expiry_pnl(priced, s) for s in points)
    return round(max(0.0, -worst), 2)
