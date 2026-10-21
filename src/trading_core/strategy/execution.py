"""Sending a multi-leg position to the broker in a safe order and journaling every order/fill."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trading_core.broker.base import OrderRequest
from trading_core.strategy.position import Leg, Position

if TYPE_CHECKING:
    from trading_core.strategy.base import AlgoContext

log = logging.getLogger(__name__)


class LegExecutor:
    def __init__(self, ctx: "AlgoContext"):
        self.ctx = ctx

    def send(self, leg: Leg, purpose: str, force: bool = False) -> bool:
        ctx = self.ctx
        side = leg.side if purpose == "ENTRY" else leg.close_side
        req = OrderRequest(instrument_token=leg.instrument_token, tradingsymbol=leg.tradingsymbol,
                           exchange=leg.exchange, side=side, qty=leg.qty, tag=ctx.algo_id[:20], force=force)
        placed_at = ctx.clock.now()
        res = ctx.broker.place_order(req)
        ctx.journal.record_order(ctx.run_id, ctx.algo_id, ctx.mode, purpose, leg.leg_id, req, res, placed_at)
        if not res.filled:
            msg = f"{purpose} {side} {leg.tradingsymbol} rejected: {res.reject_reason}"
            log.warning(msg)
            ctx.journal.event(ctx.run_id, ctx.algo_id, placed_at, "ORDER_REJECTED", msg, level="WARNING",
                              data={"leg": leg.role, "bid": res.bid, "ask": res.ask})
            return False
        if purpose == "ENTRY":
            leg.entry_price, leg.entry_charges = res.avg_price, res.charges.total
        else:
            leg.exit_price, leg.exit_charges = res.avg_price, res.charges.total
        log.info("%s %s %d %s @ %.2f (bid %s ask %s)", purpose, side, leg.qty, leg.tradingsymbol,
                 res.avg_price, res.bid, res.ask)
        return True

    def open(self, pos: Position) -> bool:
        """Fill legs hedges-first. If any leg fails, unwind what was filled and return False."""
        for leg in pos.entry_order():
            if not self.send(leg, "ENTRY"):
                if any(l.entry_price is not None for l in pos.legs):
                    log.warning("entry failed part-way; unwinding filled legs")
                    self.close(pos)
                return False
        return True

    def close(self, pos: Position) -> bool:
        """Close open legs shorts-first. If a short cannot be closed, its hedge is kept
        (never leave a naked short) and the caller retries on the next loop."""
        for leg in pos.exit_order():
            if not self.send(leg, "EXIT", force=True) and leg.is_short:
                return False
        return pos.is_flat
