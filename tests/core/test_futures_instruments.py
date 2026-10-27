from datetime import date

from trading_core.kite.instruments import InstrumentStore, filter_rows

DAY = date(2026, 10, 27)


def fut(symbol, expiry, name="NIFTY"):
    return dict(instrument_token=hash(symbol) % 10_000_000, exchange_token=None, tradingsymbol=symbol, name=name,
                exchange="NFO", segment="NFO-FUT", instrument_type="FUT", strike=0, expiry=expiry, lot_size=65,
                tick_size=0.05)


def test_futures_of_the_wanted_underlyings_are_kept_and_the_near_month_is_found(platform, db):
    nfo = [fut("NIFTY26DECFUT", date(2026, 12, 29)), fut("NIFTY26NOVFUT", date(2026, 11, 24)),
           fut("NIFTY26OCTFUT", date(2026, 10, 27)), fut("BANKNIFTY26NOVFUT", date(2026, 11, 24), "BANKNIFTY")]
    rows = filter_rows(nfo, [], ["NIFTY"])
    assert sorted(r["tradingsymbol"] for r in rows) == ["NIFTY26DECFUT", "NIFTY26NOVFUT", "NIFTY26OCTFUT"]

    store = InstrumentStore(db, platform.instruments)
    store.save_snapshot(DAY, rows)
    assert store.nearest_future("NIFTY", DAY).tradingsymbol == "NIFTY26OCTFUT"          # expires today: still near
    assert store.nearest_future("NIFTY", date(2026, 10, 28)) is None                    # no snapshot that day
    assert store.nearest_expiry("NIFTY", DAY) is None                                   # futures aren't options
