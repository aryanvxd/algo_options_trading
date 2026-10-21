from datetime import datetime

from trading_core.clock import IST
from trading_core.market.premarket import build_snapshot


def q(last, close):
    return {"last_price": last, "ohlc": {"close": close}}


def test_snapshot_gap_breadth_and_rankings():
    quotes = {"NSE:NIFTY 50": q(25100, 25000), "NSE:INDIA VIX": {"last_price": 13.2},
              "NSE:A": q(102, 100), "NSE:B": q(97, 100), "NSE:C": q(100, 100), "NSE:D": q(110, 100)}
    snap = build_snapshot(quotes, "NSE:NIFTY 50", "NSE:INDIA VIX", ["NSE:A", "NSE:B", "NSE:C", "NSE:D", "NSE:X"],
                          datetime(2026, 10, 23, 9, 8, tzinfo=IST))
    assert snap.gap_pct == 0.4 and snap.vix == 13.2 and snap.index_spot == 25100
    assert (snap.advances, snap.declines, snap.unchanged) == (2, 1, 1)
    assert [g["symbol"] for g in snap.gainers] == ["D", "A"]
    assert [l["symbol"] for l in snap.losers] == ["B"]
