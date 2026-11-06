"""Balanced-condor exit: the trailing profit stop.

Once P&L has reached ``arm_rupees``, exit if it falls ``give_rupees`` below its peak. Until then it
does nothing, so the normal swings of a young trade can't set it off. Holds the peak, so build a
fresh rule for every trade (BaseAlgo rebuilds the rules per trade).
"""

from __future__ import annotations

from trading_core.risk.engine import ExitRule, RiskContext


class ProfitTrail(ExitRule):
    name = "TRAILING_STOP"

    def __init__(self, arm_rupees: float, give_rupees: float, basis: str = "gross"):
        self.arm, self.give, self.basis = arm_rupees, give_rupees, basis
        self.peak: float | None = None

    def check(self, ctx: RiskContext) -> str | None:
        mark = ctx.mark
        if mark is None or not mark.complete:
            return None
        pnl = mark.net_pnl if self.basis == "net" else mark.gross_pnl
        self.peak = pnl if self.peak is None else max(self.peak, pnl)
        if self.peak >= self.arm and pnl <= self.peak - self.give:
            return f"{self.basis} P&L {pnl:.2f} fell {self.give:.0f} below its peak {self.peak:.2f}"
        return None
