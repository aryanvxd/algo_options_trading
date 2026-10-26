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

Exchange/Zerodha rules in force since 1 Apr 2026 (checked Oct 2026):
- Order requests must come from a static IP registered in the Kite developer profile (max two,
  changeable once a week, usable only by you / immediate family). Market data, login, order book
  and positions work from any IP, so paper trading is unaffected. Before sending any live order,
  check our public IPv4 matches ``live.static_ips`` in config and refuse otherwise (users report
  rejections when requests go out over IPv6 instead of the registered IPv4).
- Market and SL-M orders need a non-zero ``market_protection`` (-1 = Zerodha's automatic limit);
  needs kiteconnect >= 5.1.0. LIMIT orders are unaffected.
- Max 10 orders per second per client before exchange strategy registration (HTTP 429 above).
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
