"""Weekend-condor exits: a target that is a share of the credit, and the next-morning exit of a
position carried over the weekend."""

from __future__ import annotations

from datetime import time

from trading_core.risk.engine import ExitRule, RiskContext


class CreditTarget(ExitRule):
    """Exit once P&L reaches ``frac`` of the credit collected (e.g. 60% of a 12-pt credit)."""
    name = "TARGET"

    def __init__(self, frac: float, basis: str = "gross"):
        self.frac, self.basis = frac, basis

    def check(self, ctx: RiskContext) -> str | None:
        mark, credit = ctx.mark, ctx.position.entry_credit_pts
        if mark is None or not mark.complete or not credit or credit <= 0:
            return None
        pnl = mark.net_pnl if self.basis == "net" else mark.gross_pnl
        goal = credit * self.frac * ctx.position.unit_qty
        return f"{self.basis} P&L {pnl:.2f} >= {self.frac:.0%} of credit ({goal:.2f})" if pnl >= goal else None


class CarriedExit(ExitRule):
    """On a later day than the entry: buy back from ``exit_from`` at the first moment every open
    leg's bid-ask is within ``max_spread``, and at any price from ``hard_exit``."""
    name = "CARRY_EXIT"

    def __init__(self, exit_from: time, max_spread: float, hard_exit: time):
        self.exit_from, self.max_spread, self.hard_exit = exit_from, max_spread, hard_exit

    def check(self, ctx: RiskContext) -> str | None:
        entry = ctx.position.entry_time
        if entry is None or entry.date() >= ctx.now.date():
            return None                                      # still the entry day
        t = ctx.now.time()
        if t >= self.hard_exit:
            return f"carried position: hard exit at {self.hard_exit:%H:%M}"
        if t < self.exit_from or ctx.mark is None or not ctx.mark.complete:
            return None
        still_open = {l.tradingsymbol for l in ctx.position.open_legs}
        marks = [l for l in ctx.mark.legs if l.tradingsymbol in still_open]
        if marks and all(l.bid and l.ask and l.ask - l.bid <= self.max_spread + 1e-9 for l in marks):
            return f"carried position: spreads ≤ {self.max_spread} after {self.exit_from:%H:%M}"
        return None
