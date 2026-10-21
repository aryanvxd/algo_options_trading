from datetime import datetime

import pytest

from tests.helpers import FakeFeed, tick
from trading_core.broker.base import BUY, SELL, OrderRequest
from trading_core.broker.charges import ChargeCalculator
from trading_core.broker.paper import PaperBroker
from trading_core.clock import IST, SimClock
from trading_core.config import ChargeRates, PaperConfig

RATES = ChargeRates(brokerage_per_order=20, stt_sell_rate=0.0015, exchange_txn_rate=0.0003503,
                    sebi_rate=0.000001, stamp_buy_rate=0.00003, gst_rate=0.18)


def test_sell_charges_hand_calculated():
    c = ChargeCalculator(RATES).compute(SELL, 10.0, 65)      # turnover 650
    assert (c.brokerage, c.stt, c.exchange_txn, c.stamp) == (20.0, 0.98, 0.23, 0.0)
    assert c.gst == 3.64                                     # 18% of (20 + 0.2277 + 0.00065)
    assert c.total == 24.85


def test_buy_charges_have_stamp_not_stt():
    c = ChargeCalculator(RATES).compute(BUY, 10.0, 65)
    assert c.stt == 0.0 and c.stamp == 0.02
    assert c.total == 23.89


@pytest.fixture
def broker():
    feed = FakeFeed([tick(1, ltp=10.0, bid=9.9, ask=10.1), tick(2, ltp=5.0, bid=3.0, ask=9.0),
                     tick(3, ltp=4.0), tick(4, ltp=0.1, bid=0.05, ask=0.15)])
    clock = SimClock(datetime(2026, 10, 23, 9, 20, tzinfo=IST))
    return PaperBroker(feed, ChargeCalculator(RATES), PaperConfig(slippage_pts=0.5, max_spread_pts=2.0), clock)


def req(token, side, force=False):
    return OrderRequest(token, f"SYM{token}", "NFO", side, 65, force=force)


def test_sell_fills_at_bid_minus_slippage(broker):
    r = broker.place_order(req(1, SELL))
    assert r.filled and r.avg_price == 9.4 and r.charges.stt > 0


def test_buy_fills_at_ask_plus_slippage(broker):
    r = broker.place_order(req(1, BUY))
    assert r.filled and r.avg_price == 10.6
    assert broker.positions() == {1: 65}


def test_wide_spread_rejected_unless_forced(broker):
    assert broker.place_order(req(2, SELL)).status == "REJECTED"
    forced = broker.place_order(req(2, BUY, force=True))
    assert forced.filled and forced.avg_price == 9.5


def test_empty_depth_rejected_but_forced_exit_uses_ltp(broker):
    r = broker.place_order(req(3, BUY))
    assert r.status == "REJECTED" and r.reject_reason == "empty depth"
    assert broker.place_order(req(3, BUY, force=True)).avg_price == 4.5


def test_fill_price_never_below_one_tick(broker):
    assert broker.place_order(req(4, SELL)).avg_price == 0.05


def test_no_market_data_rejected(broker):
    assert broker.place_order(req(99, BUY)).reject_reason == "no market data"
