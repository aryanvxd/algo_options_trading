"""Simulated broker: fills against the live (or replayed) order book with slippage and real charges."""

from __future__ import annotations

import uuid
from collections import defaultdict

from trading_core.broker.base import BUY, Broker, OrderRequest, OrderResult
from trading_core.broker.charges import ChargeCalculator
from trading_core.clock import Clock
from trading_core.config import PaperConfig
from trading_core.feed.base import MarketFeed


def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 2)


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, feed: MarketFeed, charges: ChargeCalculator, cfg: PaperConfig, clock: Clock):
        self.feed = feed
        self.charges = charges
        self.cfg = cfg
        self.clock = clock
        self._book: dict[int, int] = defaultdict(int)

    def place_order(self, req: OrderRequest) -> OrderResult:
        order_id = str(uuid.uuid4())
        now = self.clock.now()
        tick = self.feed.latest(req.instrument_token)

        def reject(reason: str) -> OrderResult:
            return OrderResult(order_id=order_id, status="REJECTED", reject_reason=reason, filled_at=now,
                               bid=tick.bid if tick else None, ask=tick.ask if tick else None)

        if tick is None:
            return reject("no market data")

        if tick.has_depth and tick.spread <= self.cfg.max_spread_pts:
            base = tick.ask if req.side == BUY else tick.bid
        elif req.force and tick.has_depth:
            base = tick.ask if req.side == BUY else tick.bid
        elif req.force and tick.ltp:
            base = tick.ltp
        elif not tick.has_depth:
            return reject("empty depth")
        else:
            return reject(f"spread {tick.spread:.2f} > {self.cfg.max_spread_pts}")

        slip = self.cfg.slippage_pts
        price = base + slip if req.side == BUY else base - slip
        price = max(self.cfg.tick_size, round_to_tick(price, self.cfg.tick_size))

        if req.limit_price is not None and not req.force:
            if (req.side == BUY and price > req.limit_price) or (req.side != BUY and price < req.limit_price):
                return reject(f"limit {req.limit_price} not marketable at {price}")

        self._book[req.instrument_token] += req.qty if req.side == BUY else -req.qty
        return OrderResult(
            order_id=order_id,
            status="COMPLETE",
            filled_qty=req.qty,
            avg_price=price,
            bid=tick.bid,
            ask=tick.ask,
            slippage_pts=slip,
            filled_at=now,
            charges=self.charges.compute(req.side, price, req.qty),
        )

    def positions(self) -> dict[int, int]:
        return {k: v for k, v in self._book.items() if v != 0}
