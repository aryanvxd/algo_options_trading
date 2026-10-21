"""Live Kite broker — deliberately NOT implemented in v1.

Turn this on only after the paper run (6–8 weeks) meets the checklist in the plan. It must:
- place LIMIT orders (or MARKET with market protection, which Kite requires for API market orders),
  tagged with the algo_id so orders can be traced back,
- wait for each order to COMPLETE before sending the next leg (hedges before shorts; if a hedge
  does not fill, the shorts are never sent),
- check basket margin before entry (``kite.basket_order_margins``),
- reconcile ``kite.positions()`` against the algo's position book every minute and fire the kill
  switch on any mismatch,
- respect SEBI's current retail-algo rules (static IP, order-rate limits, algo registration).
"""

from __future__ import annotations

from trading_core.broker.base import Broker, OrderRequest, OrderResult


class LiveBroker(Broker):
    mode = "live"

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("Live trading is disabled in v1. Run with --mode paper.")

    def place_order(self, req: OrderRequest) -> OrderResult:  # pragma: no cover
        raise NotImplementedError

    def positions(self) -> dict[int, int]:  # pragma: no cover
        raise NotImplementedError
