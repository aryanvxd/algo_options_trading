"""Broker interface. Strategy code only talks to this, so paper and live are interchangeable."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from trading_core.broker.charges import ChargeBreakdown

BUY, SELL = "BUY", "SELL"


def opposite(side: str) -> str:
    return SELL if side == BUY else BUY


@dataclass
class OrderRequest:
    instrument_token: int
    tradingsymbol: str
    exchange: str
    side: str
    qty: int
    order_type: str = "LIMIT"
    limit_price: float | None = None
    tag: str | None = None
    # Forced orders (stop/kill/time exits) must close even if the book is wide or empty.
    force: bool = False


@dataclass
class OrderResult:
    order_id: str
    status: str                       # COMPLETE | REJECTED | OPEN | CANCELLED
    filled_qty: int = 0
    avg_price: float | None = None
    reject_reason: str | None = None
    broker_order_id: str | None = None
    bid: float | None = None
    ask: float | None = None
    slippage_pts: float | None = None
    filled_at: datetime | None = None
    charges: ChargeBreakdown = field(default_factory=ChargeBreakdown)

    @property
    def filled(self) -> bool:
        return self.status == "COMPLETE" and self.filled_qty > 0


class Broker(ABC):
    mode: str

    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult:
        """Place an order and return once it is filled, rejected or timed out."""

    @abstractmethod
    def positions(self) -> dict[int, int]:
        """Net quantity per instrument token (for reconciliation)."""
