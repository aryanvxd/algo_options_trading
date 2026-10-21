"""Per-order statutory charges and brokerage for index options (rates come from platform config)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

from trading_core.config import ChargeRates


def _r(x: float) -> float:
    """Round to paise, half-up (float ``round`` turns 0.975 into 0.97)."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: float = 0.0
    gst: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0

    @property
    def total(self) -> float:
        return _r(self.brokerage + self.gst + self.stt + self.exchange_txn + self.sebi + self.stamp)

    def as_dict(self) -> dict[str, float]:
        return {**asdict(self), "total": self.total}


class ChargeCalculator:
    def __init__(self, rates: ChargeRates):
        self.rates = rates

    def compute(self, side: str, price: float, qty: int) -> ChargeBreakdown:
        r = self.rates
        turnover = price * qty
        brokerage = r.brokerage_per_order
        exchange_txn = turnover * r.exchange_txn_rate
        sebi = turnover * r.sebi_rate
        stt = turnover * r.stt_sell_rate if side == "SELL" else 0.0
        stamp = turnover * r.stamp_buy_rate if side == "BUY" else 0.0
        gst = (brokerage + exchange_txn + sebi) * r.gst_rate
        return ChargeBreakdown(
            brokerage=_r(brokerage),
            gst=_r(gst),
            stt=_r(stt),
            exchange_txn=_r(exchange_txn),
            sebi=_r(sebi),
            stamp=_r(stamp),
        )
