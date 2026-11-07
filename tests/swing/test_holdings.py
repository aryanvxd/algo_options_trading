from datetime import datetime

import pytest

from swing.holdings import Holding, load, order_symbols, parse, review, review_row, write
from swing.universe import SwingIndex, SwingUniverse
from swing.zones import ZoneConfig
from tests.swing.test_zones import make, saw

U = SwingUniverse("t", ["AAA"], {"AAA": "Power"}, [SwingIndex("NIFTY ENERGY", "theme", ["AAA"])])
ZC = ZoneConfig()


def kite_row(symbol, qty=10, avg=100.0, last=105.0, t1=0, pledged=0, exchange="NSE"):
    return dict(tradingsymbol=symbol, exchange=exchange, quantity=qty, t1_quantity=t1, collateral_quantity=pledged,
                average_price=avg, last_price=last)


def bars(rows=None, symbol="AAA"):
    b = make(rows or saw(400) + [(104.5, 105.5, 105.0)])
    b.symbol = symbol
    return b


def test_parse_adds_t1_and_pledged_and_drops_empty_rows():
    hs = parse([kite_row("BBB", qty=5, t1=2, pledged=3), kite_row("AAA"), kite_row("ZERO", qty=0)])
    assert [(h.symbol, h.quantity) for h in hs] == [("AAA", 10), ("BBB", 10)]


def test_order_symbols_are_cash_market_only():
    gtts = [{"condition": {"exchange": "NSE", "tradingsymbol": "AAA"}}, {"condition": {"exchange": "NFO", "tradingsymbol": "OPT"}}]
    alerts = [{"lhs_exchange": "BSE", "lhs_tradingsymbol": "BBB"}, {"lhs_exchange": "INDICES", "lhs_tradingsymbol": "NIFTY 50"}]
    assert order_symbols(gtts, alerts) == {"AAA", "BBB"}


def test_row_money_and_position():
    r = review_row(Holding("AAA", "NSE", 10, 100.0, 104.0), bars(), ZC, 1.0, U)
    assert (r["close"], r["invested"], r["value"], r["pnl"], r["pnl_pct"]) == (105.0, 1000.0, 1050.0, 50.0, 5.0)
    assert r["industry"] == "Power" and r["in_universe"] and r["chart"] and r["position"]
    assert r["vs_average_atr"] == pytest.approx(5.0 / r["atr"], abs=0.01)
    assert r["average_zone"] is None                                  # 100 sits above the support at ~99.5
    z = review_row(Holding("AAA", "NSE", 10, 99.5, 104.0), bars(), ZC, 1.0, U)["average_zone"]
    assert z and z[0] <= 99.5 <= z[1]


def test_price_below_every_zone():
    b = bars(saw(400) + [(80.0, 81.0, 80.5)] * 2)
    assert review_row(Holding("AAA", "NSE", 1, 105.0, 80.0), b, ZC, 1.0, U)["no_support_below"] is True


def test_no_candles_and_short_history_are_shown_not_dropped():
    r = review_row(Holding("BSEONLY", "BSE", 4, 50.0, 60.0), None, ZC, 1.0, U)
    assert r["note"] == "no NSE candles" and r["value"] == 240.0 and r["position"] is None and not r["chart"]
    r = review_row(Holding("NEW", "NSE", 1, 100.0, 105.0), bars(saw(50), "NEW"), ZC, 1.0, U)
    assert r["position"] is None and "daily candles" in r["note"] and r["chart"]


def test_review_totals_and_round_trip(platform):
    hs = [Holding("AAA", "NSE", 10, 100.0, 104.0), Holding("BSEONLY", "BSE", 4, 50.0, 60.0)]
    data = review(hs, {"AAA": bars()}, ZC, 1.0, U, datetime(2026, 11, 6, 21, 0))
    assert data["totals"] == {"holdings": 2, "invested": 1200.0, "value": 1290.0, "pnl": 90.0, "pnl_pct": 7.5}
    assert load(platform)["available"] is False
    write(platform, data)
    assert load(platform)["rows"][0]["symbol"] == "AAA"
