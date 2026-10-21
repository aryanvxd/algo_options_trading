"""Option-chain helpers shared by option-selling algos."""

from __future__ import annotations

import math
from bisect import bisect_left
from typing import Sequence


def atm_strike(spot: float, step: float) -> float:
    """Spot rounded to the nearest strike step (half rounds up, unlike Python's banker's round)."""
    return math.floor(spot / step + 0.5) * step


def nearest_listed(target: float, listed: Sequence[float], tie: str = "down") -> float | None:
    """Closest listed strike to ``target``. ``tie`` ('down'/'up') breaks exact ties; pass the
    out-of-the-money direction so ties land further OTM (puts: down, calls: up)."""
    if not listed:
        return None
    strikes = sorted(listed)
    i = bisect_left(strikes, target)
    candidates = [strikes[j] for j in (i - 1, i) if 0 <= j < len(strikes)]
    best = min(candidates, key=lambda k: (abs(k - target), k if tie == "down" else -k))
    return best
