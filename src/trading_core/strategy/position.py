"""Multi-leg position and mark-to-market at executable prices.

P&L is computed as if every open leg were closed now at the price we would actually get:
shorts are bought back at the ask, longs are sold at the bid (falling back to LTP only when the
book is empty). This is more pessimistic than LTP and closer to what a real exit pays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from trading_core.broker.base import BUY, SELL, opposite
from trading_core.broker.charges import ChargeCalculator
from trading_core.feed.base import Tick


@dataclass
class Leg:
    role: str
    instrument_token: int
    tradingsymbol: str
    side: str                          # entry side
    qty: int
    exchange: str = "NFO"
    option_type: str | None = None
    strike: float | None = None
    leg_id: int | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    entry_charges: float = 0.0
    exit_charges: float = 0.0

    @property
    def is_short(self) -> bool:
        return self.side == SELL

    @property
    def is_open(self) -> bool:
        return self.entry_price is not None and self.exit_price is None

    @property
    def close_side(self) -> str:
        return opposite(self.side)

    def pnl_at(self, close_price: float) -> float:
        assert self.entry_price is not None
        sign = 1 if self.side == BUY else -1
        return sign * (close_price - self.entry_price) * self.qty

    @property
    def realized_pnl(self) -> float | None:
        if self.entry_price is None or self.exit_price is None:
            return None
        return self.pnl_at(self.exit_price)


@dataclass
class LegMark:
    role: str
    tradingsymbol: str
    side: str
    entry_price: float | None
    close_price: float | None
    bid: float | None
    ask: float | None
    ltp: float | None
    pnl: float | None


@dataclass
class Mark:
    ts: datetime
    net_premium: float | None          # cost to close, in points per unit (credit structures: positive)
    gross_pnl: float
    entry_charges: float
    exit_charges: float                # actual (closed legs) + estimated (open legs)
    complete: bool                     # every open leg had a usable price
    legs: list[LegMark] = field(default_factory=list)

    @property
    def charges(self) -> float:
        return round(self.entry_charges + self.exit_charges, 2)

    @property
    def net_pnl(self) -> float:
        return round(self.gross_pnl - self.charges, 2)


@dataclass
class Position:
    structure: str
    underlying: str
    legs: list[Leg]
    expiry: date | None = None
    params: dict[str, Any] = field(default_factory=dict)
    position_id: int | None = None
    status: str = "OPENING"
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None
    capital_required: float | None = None   # rupees blocked to hold it: broker margin, or premium paid
    capital_source: str | None = None       # kite_margin | estimate | premium
    spot_at_entry: float | None = None
    spot_at_exit: float | None = None

    @property
    def unit_qty(self) -> int:
        """Quantity of one unit of the structure. Legs on the same instrument (one option bought as
        lot A + lot B) add up, so a split position still reads as one option's price per unit."""
        by_token: dict[int, int] = {}
        for l in self.legs:
            by_token[l.instrument_token] = by_token.get(l.instrument_token, 0) + l.qty
        return min(by_token.values())

    @property
    def open_legs(self) -> list[Leg]:
        return [l for l in self.legs if l.is_open]

    @property
    def is_flat(self) -> bool:
        return all(not l.is_open for l in self.legs)

    def entry_order(self) -> list[Leg]:
        """Protective legs first: buys (hedges) before sells (shorts)."""
        return sorted(self.legs, key=lambda l: 0 if l.side == BUY else 1)

    def exit_order(self) -> list[Leg]:
        """Shorts are closed before their hedges, so we are never naked short."""
        return sorted(self.open_legs, key=lambda l: 0 if l.side == SELL else 1)

    @property
    def entry_credit_pts(self) -> float | None:
        """Net credit received per unit (negative for a debit structure)."""
        if any(l.entry_price is None for l in self.legs):
            return None
        total = sum((l.entry_price if l.is_short else -l.entry_price) * l.qty for l in self.legs)
        return round(total / self.unit_qty, 2)

    @property
    def exit_debit_pts(self) -> float | None:
        if any(l.exit_price is None for l in self.legs):
            return None
        total = sum((l.exit_price if l.is_short else -l.exit_price) * l.qty for l in self.legs)
        return round(total / self.unit_qty, 2)

    @property
    def total_charges(self) -> float:
        return round(sum(l.entry_charges + l.exit_charges for l in self.legs), 2)

    @property
    def realized_gross(self) -> float:
        return round(sum(l.realized_pnl or 0.0 for l in self.legs), 2)

    def mark(self, quote: Callable[[int], Tick | None], charges: ChargeCalculator, now: datetime) -> Mark:
        gross = 0.0
        entry_ch = sum(l.entry_charges for l in self.legs if l.entry_price is not None)
        exit_ch = sum(l.exit_charges for l in self.legs if l.exit_price is not None)
        premium_total = 0.0
        complete = True
        leg_marks: list[LegMark] = []

        for leg in self.legs:
            if leg.entry_price is None:
                continue
            if leg.exit_price is not None:
                close = leg.exit_price
                t = None
            else:
                t = quote(leg.instrument_token)
                close = _executable_close(leg, t)
                if close is None:
                    complete = False
                    leg_marks.append(LegMark(leg.role, leg.tradingsymbol, leg.side, leg.entry_price, None,
                                             t.bid if t else None, t.ask if t else None, t.ltp if t else None, None))
                    continue
                exit_ch += charges.compute(leg.close_side, close, leg.qty).total
            pnl = leg.pnl_at(close)
            gross += pnl
            premium_total += (close if leg.is_short else -close) * leg.qty
            leg_marks.append(LegMark(leg.role, leg.tradingsymbol, leg.side, leg.entry_price, close,
                                     t.bid if t else None, t.ask if t else None, t.ltp if t else None, round(pnl, 2)))

        net_premium = round(premium_total / self.unit_qty, 2) if complete else None
        return Mark(ts=now, net_premium=net_premium, gross_pnl=round(gross, 2), entry_charges=round(entry_ch, 2),
                    exit_charges=round(exit_ch, 2), complete=complete, legs=leg_marks)


def _executable_close(leg: Leg, t: Tick | None) -> float | None:
    if t is None:
        return None
    if t.has_depth:
        return t.ask if leg.close_side == BUY else t.bid
    return t.ltp
