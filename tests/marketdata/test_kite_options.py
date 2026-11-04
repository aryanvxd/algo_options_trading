"""The evening option download, against a fake Kite: no network."""

from datetime import date, datetime, timedelta

import pyarrow as pa
import pytest

from trading_core.clock import IST
from trading_core.db import models as m
from trading_core.kite.auth import NotLoggedIn
from trading_core.market.calendar import MarketCalendar
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.sources.kite_options import (KiteOptions, expiry_key, last_complete_session, reminder,
                                                          trading_days)
from trading_core.marketdata.store import MarketStore

MON, TUE, WED, THU = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)
E1, E2, E3, E4 = date(2026, 11, 3), date(2026, 11, 9), date(2026, 11, 17), date(2026, 11, 23)
CAL = MarketCalendar([date(2026, 11, 10)])
QUIET = dict(progress=lambda _m: None)


class FakeKite:
    """Two strikes per expiry; a contract trades on every day inside its listed range, 3 minutes a day."""

    def __init__(self, expiries=(E1, E2, E3, E4), listed_from=MON, fail=()):
        self.expiries, self.listed_from, self.fail = expiries, listed_from, set(fail)
        self.calls = []
        self.rows = [dict(instrument_token=(i + 1) * 100 + j * 10 + (1 if o == "CE" else 2), name="NIFTY",
                          segment="NFO-OPT", instrument_type=o, strike=float(k), expiry=e,
                          tradingsymbol=f"NIFTY{e:%m%d}{k}{o}")
                     for i, e in enumerate(expiries) for j, k in enumerate((23000, 23500)) for o in ("CE", "PE")]
        self.rows += [dict(instrument_token=999, name="BANKNIFTY", segment="NFO-OPT", instrument_type="CE",
                           strike=50000.0, expiry=E2, tradingsymbol="BN"),
                      dict(instrument_token=998, name="NIFTY", segment="NFO-FUT", instrument_type="FUT", strike=0.0,
                           expiry=E4, tradingsymbol="NIFTYFUT")]

    def instruments(self, exchange):
        return self.rows

    def historical(self, token, start, end, interval, oi=False, continuous=False):
        self.calls.append((token, start, end))
        assert interval == "minute" and oi
        if token in self.fail:
            raise ValueError("boom")
        out, d = [], start.date()
        while d <= end.date():
            if d >= self.listed_from and d.weekday() < 5:
                out += [dict(date=datetime(d.year, d.month, d.day, 9, 15 + i, tzinfo=IST), open=10.0, high=12.0,
                             low=9.0, close=11.0 + i, volume=5, oi=100 + i) for i in range(3)]
            d += timedelta(days=1)
        return out


@pytest.fixture
def store(platform):
    return MarketStore(platform.marketdata.dir)


def run(kite, db, store, start=MON, end=WED, today=WED, expiries=3, **kw):
    return KiteOptions(kite, store, Manifest(db), CAL, **QUIET).download("NIFTY", "NIFTY 50", start, end, today,
                                                                          expiries, **kw)


def seed_spot(store, d, close=23400.0):
    store.write_day("index_1m", "NIFTY 50", d, "kite", pa.Table.from_pylist(
        [dict(ts=datetime(d.year, d.month, d.day, 9, 15 + i), trade_date=d, symbol="NIFTY 50", open=close, high=close,
              low=close, close=close + i, volume=0, source="kite") for i in range(3)]))


def test_last_complete_session_never_stores_a_half_day():
    def at(d, h, mi):
        return datetime(d.year, d.month, d.day, h, mi, tzinfo=IST)

    assert last_complete_session(at(WED, 11, 0), CAL) == TUE
    assert last_complete_session(at(WED, 15, 34), CAL) == TUE
    assert last_complete_session(at(WED, 15, 35), CAL) == WED
    assert last_complete_session(at(date(2026, 11, 7), 20, 0), CAL) == date(2026, 11, 6)     # Saturday
    assert last_complete_session(at(date(2026, 11, 10), 20, 0), CAL) == date(2026, 11, 9)    # holiday


def test_trading_days_skip_holidays_and_weekends():
    assert trading_days(date(2026, 11, 6), date(2026, 11, 11), CAL) == [
        date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 11)]


def test_nearest_expiries_only_and_expired_contracts_are_left_out(db, store):
    kite = FakeKite()
    results = run(kite, db, store, today=WED, expiries=2)
    assert [r.expiry for r in results] == [E2, E3]             # E1 expired yesterday
    assert all(r.contracts == 4 for r in results)
    assert {c[0] for c in kite.calls} == {r["instrument_token"] for r in kite.rows
                                          if r["name"] == "NIFTY" and r["segment"] == "NFO-OPT"
                                          and r["expiry"] in (E2, E3)}


def test_an_expiry_is_still_fetched_on_its_own_evening(db, store):
    [r] = run(FakeKite(), db, store, end=TUE, today=TUE, expiries=1)
    assert r.expiry == E1 and r.new_days == 2


def test_rows_land_in_the_schema_with_spot_and_oi(db, store):
    seed_spot(store, WED)
    run(FakeKite(), db, store, start=WED, expiries=1)
    t = store.read("options_1m", "NIFTY", "kite", day=WED)
    assert t.schema == store.dataset("options_1m").schema
    assert t.num_rows == 4 * 3
    row = t.to_pylist()[0]
    assert row["ts"] == datetime(2026, 11, 4, 9, 15) and row["expiry"] == E2 and row["trade_date"] == WED
    assert (row["close"], row["oi"], row["volume"], row["spot"], row["filled"]) == (11.0, 100, 5, 23400.0, False)
    assert row["underlying"] == "NIFTY" and row["source"] == "kite"


def test_each_expiry_merges_into_the_days_file_and_keeps_its_own_bookkeeping(db, store):
    run(FakeKite(), db, store, start=WED, expiries=3)
    t = store.read("options_1m", "NIFTY", "kite", day=WED)
    assert set(t["expiry"].to_pylist()) == {E2, E3, E4}
    assert t.num_rows == 3 * 4 * 3
    man = Manifest(db)
    assert all(man.have("options_1m", expiry_key("NIFTY", e), WED, "kite") for e in (E2, E3, E4))
    with db.session() as s:
        row = s.get(m.MarketDataFile, ("options_1m", "NIFTY:2026-11-09", WED, "kite"))
    assert row.rows == 12 and row.contracts == 4


def test_a_second_run_fetches_only_the_new_day(db, store):
    run(FakeKite(), db, store, end=TUE, today=TUE, expiries=1)      # E1 only
    run(FakeKite(), db, store, end=TUE, today=WED, expiries=1)      # E2, Mon and Tue
    kite = FakeKite()
    [r] = run(kite, db, store, end=WED, today=WED, expiries=1)
    assert r.new_days == 1 and r.stored_days == 3
    assert {(c[1].date(), c[2].date()) for c in kite.calls} == {(WED, WED)}
    kite = FakeKite()
    run(kite, db, store, end=WED, today=WED, expiries=1)
    assert kite.calls == []                                          # nothing new: no requests


def test_missed_days_are_backfilled_in_one_call_per_contract(db, store):
    kite = FakeKite()
    [r] = run(kite, db, store, start=date(2026, 10, 30), end=THU, today=THU, expiries=1)
    assert r.expiry == E2 and r.new_days == 4 and r.stored_days == 4 and len(kite.calls) == 4


def test_days_before_a_contract_listed_are_not_asked_for_again(db, store):
    run(FakeKite(listed_from=WED), db, store, start=MON, end=WED, expiries=1)
    assert not store.read("options_1m", "NIFTY", "kite", day=MON).num_rows
    kite = FakeKite(listed_from=WED)
    run(kite, db, store, start=MON, end=WED, expiries=1)
    assert kite.calls == []


def test_a_failed_contract_marks_the_day_partial_and_the_next_run_repairs_it(db, store):
    bad = 200 + 1      # E2's first contract
    kite = FakeKite(fail={bad})
    [r] = run(kite, db, store, start=WED, expiries=1)
    assert len(r.failed) == 1
    key = expiry_key("NIFTY", E2)
    assert not Manifest(db).have("options_1m", key, WED, "kite")
    assert store.read("options_1m", "NIFTY", "kite", day=WED).num_rows == 3 * 3     # the other contracts are kept
    [r] = run(FakeKite(), db, store, start=WED, expiries=1)
    assert not r.failed and Manifest(db).have("options_1m", key, WED, "kite")
    assert store.read("options_1m", "NIFTY", "kite", day=WED).num_rows == 4 * 3     # replaced, not doubled


def test_a_day_with_no_candles_and_failures_is_not_called_empty(db, store):
    kite = FakeKite(fail={r["instrument_token"] for r in FakeKite().rows})
    run(kite, db, store, start=WED, expiries=1)
    assert not Manifest(db).have("options_1m", expiry_key("NIFTY", E2), WED, "kite")


def test_an_expired_login_stops_the_run(db, store):
    kite = FakeKite()

    def refuse(*a, **k):
        raise NotLoggedIn("run algo login")

    kite.historical = refuse
    with pytest.raises(NotLoggedIn):
        run(kite, db, store, expiries=1)


def snapshot(db, expiry):
    with db.session() as s:
        s.add(m.Instrument(as_of_date=MON, instrument_token=1, tradingsymbol="X", name="NIFTY", exchange="NFO",
                           segment="NFO-OPT", instrument_type="CE", strike=23000.0, expiry=expiry, lot_size=65,
                           tick_size=0.05))


def test_login_reminder_on_expiry_day(db):
    snapshot(db, E2)
    note = reminder(db, Manifest(db), "NIFTY", CAL, datetime(2026, 11, 9, 9, 35, tzinfo=IST))
    assert "expiry" in note and "this evening" in note


def test_login_reminder_counts_unfetched_days_and_goes_quiet_when_current(db, store):
    snapshot(db, E2)
    run(FakeKite(), db, store, start=MON, end=MON, today=WED, expiries=1)
    note = reminder(db, Manifest(db), "NIFTY", CAL, datetime(2026, 11, 5, 9, 35, tzinfo=IST))
    assert "2 finished day(s)" in note and "Mon 09 Nov" in note
    run(FakeKite(), db, store, start=MON, end=WED, today=WED, expiries=1)
    assert reminder(db, Manifest(db), "NIFTY", CAL, datetime(2026, 11, 5, 9, 35, tzinfo=IST)) is None


def test_login_reminder_with_nothing_stored_yet(db):
    snapshot(db, E2)
    note = reminder(db, Manifest(db), "NIFTY", CAL, datetime(2026, 11, 5, 9, 35, tzinfo=IST))
    assert "no days stored yet" in note
    assert reminder(db, Manifest(db), "BANKNIFTY", CAL, datetime(2026, 11, 5, 9, 35, tzinfo=IST)) is None
