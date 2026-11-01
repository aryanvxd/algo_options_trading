from datetime import date, datetime

import pyarrow as pa
import pytest

from swing.bars import avg_traded_value_cr, daily_bars, weekly
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.sources.kite_index import KiteHistory
from trading_core.marketdata.store import MarketStore

# Mon 2026-10-19 .. Fri 2026-10-23, then Mon 2026-10-26 .. Wed 2026-10-28 (a short week)
DAYS = [date(2026, 10, d) for d in (19, 20, 21, 22, 23, 26, 27, 28)]


@pytest.fixture
def store(platform):
    return MarketStore(platform.marketdata.dir)


def seed(store, symbol="POWERGRID", universe="swing"):
    rows = [dict(trade_date=d, symbol=symbol, open=100.0 + i, high=105.0 + i, low=95.0 + i, close=101.0 + i,
                 volume=1000 * (i + 1), source="kite") for i, d in enumerate(DAYS)]
    rows.append(dict(trade_date=date(2025, 12, 31), symbol=symbol, open=90.0, high=91.0, low=89.0, close=90.0,
                     volume=10, source="kite"))       # another year's file
    for year in (2025, 2026):
        store.write_year("equity_daily", universe, year, "kite",
                         pa.Table.from_pylist([r for r in rows if r["trade_date"].year == year]),
                         ("trade_date", "symbol"))


def test_daily_bars_reads_all_years_sorted(store):
    seed(store)
    seed(store, "NTPC")
    bars = daily_bars(store, "swing", ["POWERGRID"])
    assert list(bars) == ["POWERGRID"]
    b = bars["POWERGRID"]
    assert b.dates[0] == date(2025, 12, 31) and b.dates[1:] == DAYS
    assert b.close[-1] == 108.0
    assert len(b.since(date(2026, 10, 26))) == 3
    assert daily_bars(store, "nothing_here") == {}


def test_weekly_bars(store):
    seed(store)
    w = weekly(daily_bars(store, "swing")["POWERGRID"].since(date(2026, 1, 1)))
    assert w.dates == [date(2026, 10, 23), date(2026, 10, 28)]     # dated by the last trading day
    assert list(w.open) == [100.0, 105.0]
    assert list(w.high) == [109.0, 112.0] and list(w.low) == [95.0, 100.0]
    assert list(w.close) == [105.0, 108.0]
    assert list(w.volume) == [15000.0, 21000.0]


def test_avg_traded_value(store):
    seed(store)
    b = daily_bars(store, "swing")["POWERGRID"]
    assert avg_traded_value_cr(b, days=1) == pytest.approx(108.0 * 8000 / 1e7)


class FakeClient:
    def __init__(self):
        self.tokens = []

    def instruments(self, exchange):
        return [{"tradingsymbol": "NIFTY ENERGY", "segment": "INDICES", "instrument_token": 261641},
                {"tradingsymbol": "POWERGRID", "segment": "NSE", "instrument_token": 3834113}]

    def historical(self, token, start, end, interval):
        self.tokens.append(token)
        return [{"date": datetime(2026, 10, 23), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]


def test_equity_daily_index_segment(store, db):
    client = FakeClient()
    kh = KiteHistory(client, store, Manifest(db), progress=lambda _: None)
    out = kh.equity_daily("swing_indices", ["NIFTY ENERGY"], date(2026, 10, 1), date(2026, 10, 31),
                          segment="INDICES")
    assert out == dict(years=1, rows=1, missing=0) and client.tokens == [261641]
    b = daily_bars(store, "swing_indices")["NIFTY ENERGY"]
    assert b.close[-1] == 1.5 and b.volume[-1] == 0
    # the default is still stocks: an index name isn't found in the NSE segment
    assert kh.equity_daily("swing", ["NIFTY ENERGY"], date(2026, 10, 1), date(2026, 10, 31))["missing"] == 1
