"""Reusable exit rules. Algo-specific rules live in the algo's own ``exits.py``."""

from __future__ import annotations

from datetime import time

from trading_core.risk.engine import ExitRule, RiskContext


def _pnl(ctx: RiskContext, basis: str) -> float | None:
    if ctx.mark is None or not ctx.mark.complete:
        return None      # never act on a partial mark; StaleFeed covers prolonged gaps
    return ctx.mark.net_pnl if basis == "net" else ctx.mark.gross_pnl


class KillSwitch(ExitRule):
    name = "KILL_SWITCH"

    def check(self, ctx: RiskContext) -> str | None:
        return f"kill switch: {ctx.kill_reason or 'manual'}" if ctx.kill_requested else None


class StaleFeed(ExitRule):
    name = "STALE_FEED"

    def __init__(self, max_age_s: float):
        self.max_age_s = max_age_s

    def check(self, ctx: RiskContext) -> str | None:
        if ctx.feed_age_s is None or ctx.feed_age_s > self.max_age_s:
            age = "never" if ctx.feed_age_s is None else f"{ctx.feed_age_s:.0f}s"
            return f"feed stale ({age} > {self.max_age_s:.0f}s)"
        return None


class PnlStop(ExitRule):
    name = "STOP_LOSS"

    def __init__(self, rupees: float, basis: str = "net"):
        self.rupees, self.basis = abs(rupees), basis

    def check(self, ctx: RiskContext) -> str | None:
        pnl = _pnl(ctx, self.basis)
        if pnl is not None and pnl <= -self.rupees:
            return f"{self.basis} P&L {pnl:.2f} <= -{self.rupees:.2f}"
        return None


class PnlTarget(ExitRule):
    name = "TARGET"

    def __init__(self, rupees: float, basis: str = "net"):
        self.rupees, self.basis = rupees, basis

    def check(self, ctx: RiskContext) -> str | None:
        pnl = _pnl(ctx, self.basis)
        if pnl is not None and pnl >= self.rupees:
            return f"{self.basis} P&L {pnl:.2f} >= {self.rupees:.2f}"
        return None


class MaxDailyLoss(ExitRule):
    name = "MAX_DAILY_LOSS"

    def __init__(self, rupees: float):
        self.rupees = abs(rupees)

    def check(self, ctx: RiskContext) -> str | None:
        if ctx.mark is None or not ctx.mark.complete:
            return None
        total = ctx.day_realized_pnl + ctx.mark.net_pnl
        return f"day P&L {total:.2f} <= -{self.rupees:.2f}" if total <= -self.rupees else None


class TimeExit(ExitRule):
    name = "TIME_EXIT"

    def __init__(self, at: time):
        self.at = at

    def check(self, ctx: RiskContext) -> str | None:
        return f"time {ctx.now:%H:%M:%S} >= {self.at:%H:%M}" if ctx.now.time() >= self.at else None
