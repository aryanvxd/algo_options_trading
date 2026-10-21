"""Exit-rule engine: rules are checked in priority order on every loop; the first match wins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from trading_core.strategy.position import Mark, Position


@dataclass
class RiskContext:
    now: datetime
    position: Position
    mark: Mark | None
    feed_age_s: float | None
    kill_requested: bool = False
    kill_reason: str | None = None
    day_realized_pnl: float = 0.0


@dataclass(frozen=True)
class ExitSignal:
    rule: str
    reason: str


class ExitRule(ABC):
    name: str = "RULE"

    @abstractmethod
    def check(self, ctx: RiskContext) -> str | None:
        """Return a human-readable reason to exit, or None."""


class RiskEngine:
    def __init__(self, rules: Sequence[ExitRule]):
        self.rules = list(rules)

    def evaluate(self, ctx: RiskContext) -> ExitSignal | None:
        for rule in self.rules:
            reason = rule.check(ctx)
            if reason:
                return ExitSignal(rule.name, reason)
        return None
