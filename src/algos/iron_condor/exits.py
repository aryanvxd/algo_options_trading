"""Iron-condor-specific exit: premium blow-out.

Exits when the cost to close the condor rises ``mult`` × above the entry credit, or when any
short leg's buy-back price reaches ``short_mult`` × its entry price. This catches a fast trending
move before slippage carries the rupee stop far past its level.
"""

from __future__ import annotations

from trading_core.broker.base import SELL
from trading_core.risk.engine import ExitRule, RiskContext


class PremiumBlowout(ExitRule):
    name = "PREMIUM_BLOWOUT"

    def __init__(self, mult: float, short_mult: float):
        self.mult, self.short_mult = mult, short_mult

    def check(self, ctx: RiskContext) -> str | None:
        mark, credit = ctx.mark, ctx.position.entry_credit_pts
        if mark is None or not mark.complete or not credit:
            return None
        if mark.net_premium is not None and mark.net_premium >= credit * self.mult:
            return f"net premium {mark.net_premium:.2f} >= {self.mult} × credit {credit:.2f}"
        for leg in mark.legs:
            if leg.side == SELL and leg.close_price and leg.entry_price and \
                    leg.close_price >= leg.entry_price * self.short_mult:
                return (f"{leg.tradingsymbol} at {leg.close_price:.2f} >= {self.short_mult} × entry "
                        f"{leg.entry_price:.2f}")
        return None
