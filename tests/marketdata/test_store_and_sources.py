import csv
import io
import zipfile
from datetime import date, datetime

import pyarrow as pa
import pytest

from trading_core.marketdata.duck import MarketDB
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.sources import nse_bhavcopy
from trading_core.marketdata.sources.kite_index import KiteHistory
from trading_core.marketdata.store import MarketStore, slug

D1, D2 = date(2026, 10, 19), date(2026, 10, 21)


@pytest.fixture
def store(platform):
    return MarketStore(platform.marketdata.dir)


def index_rows(day, n=3, base=25000.0, symbol="NIFTY 50"):
    return [dict(ts=datetime(day.year, day.month, day.day, 9, 15 + i), trade_date=day, symbol=symbol,
                 open=base + i, high=base + i + 1, low=base + i - 1, close=base + i, volume=0, source="kite")
            for i in range(n)]


def test_write_read_conform_and_atomic(store):
    table = pa.Table.from_pylist(index_rows(D1))
    path, written = store.write_day("index_1m", "NIFTY 50", D1, "kite", table.drop(["volume"]))
    assert path.name == "part-kite.parquet" and "symbol=NIFTY_50" in path.as_posix()
    assert written.schema == store.dataset("index_1m").schema       # missing column added as null
    assert store.read("index_1m", "NIFTY 50", "kite", day=D1).num_rows == 3
    assert not list(path.parent.glob("*.tmp"))
    assert store.days("index_1m", "NIFTY 50") == [D1]
    assert slug("INDIA VIX") == "INDIA_VIX"


def test_yearly_merge_replaces_overlap(store):
    rows = [dict(trade_date=date(2026, 1, d), symbol="INFY", open=1, high=1, low=1, close=float(d), volume=1)
            for d in (1, 2)]
    store.write_year("equity_daily", "nifty50", 2026, "kite", pa.Table.from_pylist(rows), ("trade_date", "symbol"))
    rows2 = [dict(trade_date=date(2026, 1, 2), symbol="INFY", open=1, high=1, low=1, close=99.0, volume=1),
             dict(trade_date=date(2026, 1, 3), symbol="INFY", open=1, high=1, low=1, close=3.0, volume=1)]
    _, merged = store.write_year("equity_daily", "nifty50", 2026, "kite", pa.Table.from_pylist(rows2),
                                 ("trade_date", "symbol"))
    assert [(r["trade_date"].day, r["close"]) for r in merged.to_pylist()] == [(1, 1.0), (2, 99.0), (3, 3.0)]


def test_duckdb_views_empty_and_filled(store):
    mdb = MarketDB(store)
    assert mdb.rows("SELECT count(*) FROM options_eod")[0][0] == 0           # empty view, right schema
    store.write_day("index_1m", "NIFTY 50", D1, "kite", pa.Table.from_pylist(index_rows(D1)))
    store.write_day("index_1m", "NIFTY 50", D2, "kite", pa.Table.from_pylist(index_rows(D2, base=25100)))
    mdb.refresh()
    day = mdb.index_day("NIFTY 50", D2)
    assert day.num_rows == 3 and day["close"][0].as_py() == 25100


def test_manifest_records_and_summarises(db, store):
    man = Manifest(db)
    path, table = store.write_day("index_1m", "NIFTY 50", D1, "kite", pa.Table.from_pylist(index_rows(D1)))
    man.record("index_1m", "NIFTY 50", D1, "kite", path, table)
    path2, empty = store.write_day("index_1m", "NIFTY 50", D2, "kite", pa.Table.from_pylist([]))
    man.record("index_1m", "NIFTY 50", D2, "kite", path2, empty)
    assert man.have("index_1m", "NIFTY 50", D1, "kite") and man.have("index_1m", "NIFTY 50", D2, "kite")
    assert man.dates("index_1m", "NIFTY 50") == [D1]                         # EMPTY days are not data days
    s = man.summary()[0]
    assert (s["days"], s["rows"], s["first"], s["last"]) == (2, 3, D1, D2)


# ---------------------------------------------------------------- NSE bhavcopy

UDIFF_HEADER = ["TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs", "XpryDt",
                "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric",
                "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol",
                "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4"]
OLD_HEADER = ["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "OPEN", "HIGH", "LOW", "CLOSE",
              "SETTLE_PR", "CONTRACTS", "VAL_INLAKH", "OPEN_INT", "CHG_IN_OI", "TIMESTAMP", ""]


def zipped(header, rows, name="x.csv"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(name, buf.getvalue())
    return out.getvalue()


def udiff_row(sym, tp, strike, opt, close, expiry="2026-10-27"):
    r = dict.fromkeys(UDIFF_HEADER, "")
    r.update(TradDt="2026-10-22", FinInstrmTp=tp, TckrSymb=sym, XpryDt=expiry, StrkPric=f"{strike:.2f}", OptnTp=opt,
             OpnPric="1", HghPric="2", LwPric="0.5", ClsPric=str(close), SttlmPric=str(close), UndrlygPric="22687.65",
             OpnIntrst="650", ChngInOpnIntrst="65", TtlTradgVol="12", NewBrdLotQty="65")
    return [r[h] for h in UDIFF_HEADER]


def test_url_switches_format_on_8_july_2024():
    assert nse_bhavcopy.url_for(date(2024, 7, 5)).endswith("/2024/JUL/fo05JUL2024bhav.csv.zip")
    assert nse_bhavcopy.url_for(date(2024, 7, 8)).endswith("BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip")


def test_parse_udiff_keeps_only_index_options_of_underlying():
    raw = zipped(UDIFF_HEADER, [udiff_row("NIFTY", "IDO", 22700, "CE", 155.2), udiff_row("NIFTY", "IDF", 0, "", 22700),
                                udiff_row("BANKNIFTY", "IDO", 50000, "PE", 10), udiff_row("NIFTY", "IDO", 22700, "PE", 144.95)])
    t = nse_bhavcopy.parse(date(2026, 10, 22), raw, ["NIFTY"]).to_pylist()
    assert [(r["strike"], r["opt_type"], r["close"]) for r in t] == [(22700.0, "CE", 155.2), (22700.0, "PE", 144.95)]
    assert t[0]["expiry"] == date(2026, 10, 27) and t[0]["lot_size"] == 65 and t[0]["contracts"] == 12
    assert t[0]["spot"] == 22687.65 and t[0]["oi"] == 650


def test_parse_old_format():
    rows = [["FUTIDX", "NIFTY", "27-Jul-2023", "0", "XX", "1", "1", "1", "19646.9", "19659.9", "1", "1", "1", "1", "27-JUL-2023", ""],
            ["OPTIDX", "NIFTY", "03-Aug-2023", "19700", "CE", "90", "120", "80", "100.5", "100.5", "516", "1", "13950", "-12950", "27-JUL-2023", ""]]
    t = nse_bhavcopy.parse(date(2023, 7, 27), zipped(OLD_HEADER, rows), ["NIFTY"]).to_pylist()
    assert len(t) == 1 and t[0]["expiry"] == date(2023, 8, 3) and t[0]["contracts"] == 516 and t[0]["lot_size"] is None


class FakeResp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeSession:
    def __init__(self, files):
        self.files, self.calls, self.headers = files, [], {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        for day, raw in self.files.items():
            if day.strftime("%Y%m%d") in url:
                return FakeResp(200, raw)
        return FakeResp(404)


def test_bhavcopy_download_records_holidays_enriches_and_resumes(db, store):
    strikes = range(22300, 23150, 50)
    rows = []
    from trading_core.marketdata.greeks import price
    for k in strikes:
        for opt in ("CE", "PE"):
            rows.append(udiff_row("NIFTY", "IDO", k, opt, round(float(price(22710, k, 5 / 365, 0.065, 0.14, opt == "CE")), 2)))
    session = FakeSession({date(2026, 10, 22): zipped(UDIFF_HEADER, rows)})
    nse = nse_bhavcopy.NseBhavcopy(store, Manifest(db), ["NIFTY"], 0.065, request_interval_s=0, session=session)
    stats = nse.download(date(2026, 10, 20), date(2026, 10, 25))
    assert stats == dict(days=1, rows=len(rows), skipped=0, holidays=3, failed=0)   # 20, 21, 23 404; weekend skipped
    eod = MarketDB(store).eod_chain("NIFTY", date(2026, 10, 22)).to_pylist()
    assert len(eod) == len(rows) and all(r["iv"] is not None for r in eod if abs(r["strike"] - 22710) < 300)
    calls = len(session.calls)
    assert nse.download(date(2026, 10, 20), date(2026, 10, 25))["skipped"] == 4
    assert len(session.calls) == calls                                               # nothing re-fetched


# ---------------------------------------------------------------- Kite history

class FakeKite:
    def __init__(self):
        self.calls = []

    def historical(self, token, start, end, interval, oi=False, continuous=False):
        self.calls.append((token, start, end, interval) + (("continuous",) if continuous else ()))
        if interval == "minute":
            out = []
            for d in (date(2026, 10, 19), date(2026, 10, 21)):        # 20th (Tuesday) is a holiday
                for i in range(3):
                    out.append(dict(date=datetime(d.year, d.month, d.day, 9, 15 + i).astimezone(),
                                    open=1.0, high=1.0, low=1.0, close=10.0 + i, volume=0))
            return [c for c in out if start.date() <= c["date"].date() <= end.date()]
        return [dict(date=datetime(2026, 1, 2), open=1, high=1, low=1, close=5, volume=10)]

    def instruments(self, exchange):
        if exchange == "NFO":
            return [dict(tradingsymbol="NIFTY26NOVFUT", name="NIFTY", segment="NFO-FUT", instrument_token=111,
                         expiry=date(2099, 11, 24)),
                    dict(tradingsymbol="NIFTY26DECFUT", name="NIFTY", segment="NFO-FUT", instrument_token=222,
                         expiry=date(2099, 12, 29))]
        return [dict(tradingsymbol="INFY", segment="NSE", instrument_token=408065)]


def test_kite_index_minute_download_and_resume(db, store):
    kite = FakeKite()
    kh = KiteHistory(kite, store, Manifest(db), progress=lambda _m: None)
    stats = kh.index_1m("NIFTY 50", date(2026, 10, 19), date(2026, 10, 21))
    assert stats == dict(days=2, rows=6, skipped=0, empty=1)
    assert kite.calls[0][0] == 256265
    assert store.days("index_1m", "NIFTY 50") == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert kh.index_1m("NIFTY 50", date(2026, 10, 19), date(2026, 10, 21))["skipped"] == 3
    assert len(kite.calls) == 1                                                       # resume: no new request


def test_kite_constituents_daily(db, store):
    kh = KiteHistory(FakeKite(), store, Manifest(db), progress=lambda _m: None)
    assert kh.equity_daily("nifty50", ["INFY", "GONE"], date(2026, 1, 1), date(2026, 1, 31)) == \
        dict(years=1, rows=1, missing=1)
    assert store.read("equity_daily", "nifty50", "kite", year=2026).num_rows == 1


def test_kite_futures_and_stock_minutes_land_in_their_datasets(db, store):
    kite = FakeKite()
    hist = KiteHistory(kite, store, Manifest(db), progress=lambda m: None)
    fut = hist.futures_1m("NIFTY", date(2026, 10, 19), date(2026, 10, 21))
    assert fut["days"] == 2 and kite.calls[-1][0] == 111 and kite.calls[-1][-1] != "continuous"   # near month
    assert store.days("index_1m", "NIFTY FUT") == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    eq = hist.equity_1m(["INFY", "GONE"], date(2026, 10, 19), date(2026, 10, 21))
    assert eq["missing"] == 1 and eq["days"] == 2
    assert store.read("equity_1m", "INFY", "kite", day=date(2026, 10, 21))["close"].to_pylist() == [10.0, 11.0, 12.0]
    calls = len(kite.calls)
    hist.equity_1m(["INFY"], date(2026, 10, 19), date(2026, 10, 21))                 # resumes: nothing fetched
    assert len(kite.calls) == calls
