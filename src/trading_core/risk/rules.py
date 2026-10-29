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

    def __init__(self, max_age_s: float, market_hours: tuple[time, time] | None = None):
        self.max_age_s = max_age_s
        self.market_hours = market_hours        # only judge the feed while the market is open

    def check(self, ctx: RiskContext) -> str | None:
        if self.market_hours and not (self.market_hours[0] <= ctx.now.time() < self.market_hours[1]):
            return None
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


class TrailingStop(ExitRule):
    """Once P&L has reached ``activate_rupees``, exit if it falls back to ``keep_fraction`` of its peak.

    Holds the peak, so build a fresh rule for every trade (BaseAlgo rebuilds the rules per trade)."""
    name = "TRAILING_STOP"

    def __init__(self, activate_rupees: float, keep_fraction: float, basis: str = "net"):
        self.activate, self.keep, self.basis = activate_rupees, keep_fraction, basis
        self.peak: float | None = None

    def check(self, ctx: RiskContext) -> str | None:
        pnl = _pnl(ctx, self.basis)
        if pnl is None:
            return None
        self.peak = pnl if self.peak is None else max(self.peak, pnl)
        if self.peak >= self.activate and pnl <= self.peak * self.keep:
            return f"{self.basis} P&L {pnl:.2f} fell to {self.keep:.0%} of its peak {self.peak:.2f}"
        return None


class Stall(ExitRule):
    """Exit when P&L has made no new high for ``seconds``: the move that justified the trade is over."""
    name = "STALL"

    def __init__(self, seconds: float, basis: str = "net"):
        self.seconds, self.basis = seconds, basis
        self.peak: float | None = None
        self.peak_at = None

    def check(self, ctx: RiskContext) -> str | None:
        pnl = _pnl(ctx, self.basis)
        if pnl is None:
            return None
        if self.peak is None or pnl > self.peak:
            self.peak, self.peak_at = pnl, ctx.now
            return None
        idle = (ctx.now - self.peak_at).total_seconds()
        return f"no new P&L high for {idle:.0f}s (peak {self.peak:.2f})" if idle >= self.seconds else None


class MaxHold(ExitRule):
    name = "MAX_HOLD"

    def __init__(self, seconds: float):
        self.seconds = seconds

    def check(self, ctx: RiskContext) -> str | None:
        start = ctx.position.entry_time
        if start is None:
            return None
        held = (ctx.now - start).total_seconds()
        return f"held {held:.0f}s >= {self.seconds:.0f}s" if held >= self.seconds else None


class TimeExit(ExitRule):
    name = "TIME_EXIT"

    def __init__(self, at: time):
        self.at = at

    def check(self, ctx: RiskContext) -> str | None:
        return f"time {ctx.now:%H:%M:%S} >= {self.at:%H:%M}" if ctx.now.time() >= self.at else None
