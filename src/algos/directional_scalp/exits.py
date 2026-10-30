"""Directional-scalp exit rules (the shared ones are in trading_core/risk/rules.py).

Premium rules look at the option's executable price (bid) against its entry price, per leg, so
they work the same with one lot or with lot A / lot B. Chart rules (Renko reversal, time stop,
bias flip) ask the algo, which owns the chart and the bias score.
"""

from __future__ import annotations

from typing import Callable

from trading_core.risk.engine import ExitRule, RiskContext


def _open_legs(ctx: RiskContext):
    if ctx.mark is None or not ctx.mark.complete:
        return []
    return [l for l in ctx.mark.legs if l.close_price is not None and l.entry_price is not None
            and any(p.is_open and p.role == l.role for p in ctx.position.legs)]


class HardPremiumStop(ExitRule):
    name = "STOP_LOSS"

    def __init__(self, points: float):
        self.points = points

    def check(self, ctx: RiskContext) -> str | None:
        for l in _open_legs(ctx):
            if l.close_price <= l.entry_price - self.points:
                return f"{l.tradingsymbol} {l.close_price:.2f} <= entry {l.entry_price:.2f} - {self.points:g}"
        return None


class RunnerTarget(ExitRule):
    """Lot B's cap, once lot A is booked (with one leg only, the same cap applies to it)."""
    name = "TARGET"

    def __init__(self, points: float):
        self.points = points

    def check(self, ctx: RiskContext) -> str | None:
        for l in _open_legs(ctx):
            if l.close_price >= l.entry_price + self.points:
                return f"{l.tradingsymbol} {l.close_price:.2f} >= entry {l.entry_price:.2f} + {self.points:g}"
        return None


class BreakevenStop(ExitRule):
    """After lot A is booked, the rest may not turn into a loss."""
    name = "BREAKEVEN_STOP"

    def __init__(self, buffer_pts: float, armed: Callable[[], bool]):
        self.buffer_pts, self.armed = buffer_pts, armed

    def check(self, ctx: RiskContext) -> str | None:
        if not self.armed():
            return None
        for l in _open_legs(ctx):
            if l.close_price <= l.entry_price + self.buffer_pts:
                return (f"lot A booked; {l.tradingsymbol} {l.close_price:.2f} back to entry "
                        f"{l.entry_price:.2f} + {self.buffer_pts:g}")
        return None


class AlgoRule(ExitRule):
    """An exit the algo decides from its own state (``reason()`` returns why, or None)."""

    def __init__(self, name: str, reason: Callable[[RiskContext], str | None]):
        self.name, self.reason = name, reason

    def check(self, ctx: RiskContext) -> str | None:
        return self.reason(ctx)
