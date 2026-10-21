"""Pre-market TRADE/SKIP decision: VIX, index gap, advance/decline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from algos.iron_condor.settings import FilterConfig
from trading_core.market.premarket import PremarketSnapshot


@dataclass
class Check:
    name: str
    passed: bool
    value: float | None = None
    threshold: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    trade: bool
    checks: list[Check]
    reason: str | None = None


def evaluate(snap: PremarketSnapshot, cfg: FilterConfig) -> Decision:
    checks: list[Check] = []

    if snap.vix is None:
        checks.append(Check("vix", False, None, cfg.vix_max, {"note": "VIX unavailable"}))
    else:
        checks.append(Check("vix", snap.vix < cfg.vix_max, snap.vix, cfg.vix_max))

    if cfg.gap_filter:
        if snap.gap_pct is None:
            checks.append(Check("gap_pct", True, None, cfg.max_gap_pct, {"note": "no gap data, not enforced"}))
        else:
            checks.append(Check("gap_pct", abs(snap.gap_pct) <= cfg.max_gap_pct, snap.gap_pct, cfg.max_gap_pct))

    if cfg.breadth_filter:
        seen = snap.constituents_seen
        if seen == 0:
            checks.append(Check("adv_dec", True, None, cfg.adv_dec_extreme_ratio,
                                {"note": "no breadth data, not enforced"}))
        else:
            ratio = max(snap.advances, snap.declines) / seen
            checks.append(Check("adv_dec", ratio < cfg.adv_dec_extreme_ratio, round(ratio, 3),
                                cfg.adv_dec_extreme_ratio,
                                {"advances": snap.advances, "declines": snap.declines, "seen": seen}))

    failed = [c for c in checks if not c.passed]
    reason = None
    if failed:
        reason = "; ".join(f"{c.name}={c.value} vs {c.threshold}" + (f" ({c.detail['note']})" if 'note' in c.detail else "")
                           for c in failed)
    return Decision(trade=not failed, checks=checks, reason=reason)
