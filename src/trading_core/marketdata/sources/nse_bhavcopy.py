"""NSE F&O bhavcopy (end-of-day, every contract, free) -> ``options_eod`` with IV/greeks.

Two formats:
- before 8 Jul 2024: ``fo<DD><MON><YYYY>bhav.csv.zip`` (INSTRUMENT, SYMBOL, EXPIRY_DT, ...)
- from 8 Jul 2024:  UDiFF ``BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`` (FinInstrmTp, TckrSymb, ...)
Units (checked against real files): ``oi`` is in units (shares), ``contracts`` is lots traded.
A 404 means no trading that day (holiday) and is recorded as EMPTY.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta
from typing import Callable

import pyarrow as pa
import pyarrow.compute as pc
import requests

from trading_core.marketdata.greeks import enrich
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)

UDIFF_FROM = date(2024, 7, 8)
SOURCE = "nse"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}


def url_for(day: date) -> str:
    if day >= UDIFF_FROM:
        return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    mon = day.strftime("%b").upper()
    return (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{day:%Y}/{mon}/"
            f"fo{day:%d}{mon}{day:%Y}bhav.csv.zip")


def _num(v: str) -> float | None:
    try:
        return float(v) if v not in ("", "-") else None
    except ValueError:
        return None


def parse(day: date, raw_zip: bytes, underlyings: list[str]) -> pa.Table:
    """Option rows for ``underlyings`` from either bhavcopy format."""
    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    text = zf.read(zf.namelist()[0]).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    wanted = set(underlyings)
    out: dict[str, list] = {k: [] for k in (
        "trade_date", "underlying", "expiry", "strike", "opt_type", "open", "high", "low", "close", "settle",
        "spot", "oi", "oi_change", "contracts", "lot_size", "source")}

    def add(**kw):
        for k, v in kw.items():
            out[k].append(v)

    if "FinInstrmTp" in (reader.fieldnames or []):
        for r in reader:
            if r["FinInstrmTp"] != "IDO" or r["TckrSymb"] not in wanted:
                continue
            add(trade_date=day, underlying=r["TckrSymb"], expiry=date.fromisoformat(r["XpryDt"]),
                strike=_num(r["StrkPric"]), opt_type=r["OptnTp"], open=_num(r["OpnPric"]), high=_num(r["HghPric"]),
                low=_num(r["LwPric"]), close=_num(r["ClsPric"]), settle=_num(r["SttlmPric"]),
                spot=_num(r["UndrlygPric"]), oi=int(_num(r["OpnIntrst"]) or 0),
                oi_change=int(_num(r["ChngInOpnIntrst"]) or 0), contracts=int(_num(r["TtlTradgVol"]) or 0),
                lot_size=int(_num(r["NewBrdLotQty"]) or 0) or None, source=SOURCE)
    else:
        for r in reader:
            if r.get("INSTRUMENT") != "OPTIDX" or r.get("SYMBOL") not in wanted:
                continue
            add(trade_date=day, underlying=r["SYMBOL"],
                expiry=datetime.strptime(r["EXPIRY_DT"].strip(), "%d-%b-%Y").date(),
                strike=_num(r["STRIKE_PR"]), opt_type=r["OPTION_TYP"], open=_num(r["OPEN"]), high=_num(r["HIGH"]),
                low=_num(r["LOW"]), close=_num(r["CLOSE"]), settle=_num(r["SETTLE_PR"]), spot=None,
                oi=int(_num(r["OPEN_INT"]) or 0), oi_change=int(_num(r["CHG_IN_OI"]) or 0),
                contracts=int(_num(r["CONTRACTS"]) or 0), lot_size=None, source=SOURCE)
    return pa.table(out)


class NseBhavcopy:
    def __init__(self, store: MarketStore, manifest: Manifest, underlyings: list[str], risk_free_rate: float,
                 request_interval_s: float = 1.0, session: requests.Session | None = None,
                 progress: Callable[[str], None] | None = None):
        self.store = store
        self.manifest = manifest
        self.underlyings = underlyings
        self.r = risk_free_rate
        self.interval = request_interval_s
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.progress = progress or log.info

    def fetch(self, day: date) -> bytes | None:
        for attempt in range(3):
            try:
                resp = self.session.get(url_for(day), timeout=30)
            except requests.RequestException as e:
                log.warning("NSE %s: %s (attempt %d)", day, e, attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code == 200 and resp.content[:2] == b"PK":
                return resp.content
            log.warning("NSE %s: HTTP %s (attempt %d)", day, resp.status_code, attempt + 1)
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"could not download bhavcopy for {day}")

    def download(self, start: date, end: date, force: bool = False) -> dict[str, int]:
        stats = dict(days=0, rows=0, skipped=0, holidays=0, failed=0)
        day = start
        while day <= end:
            if day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            if not force and all(self.manifest.have("options_eod", u, day, SOURCE) for u in self.underlyings):
                stats["skipped"] += 1
                day += timedelta(days=1)
                continue
            try:
                raw = self.fetch(day)
            except RuntimeError as e:
                log.error("%s", e)
                stats["failed"] += 1
                day += timedelta(days=1)
                continue
            table_all = parse(day, raw, self.underlyings) if raw else None
            for u in self.underlyings:
                if table_all is None:
                    table = parse_empty()
                    note = "no bhavcopy (holiday)"
                else:
                    table = table_all.filter(pc.equal(table_all["underlying"], u))
                    table = enrich(table, self.r, price_col="close", ts_col="ts")
                    note = None
                path, table = self.store.write_day("options_eod", u, day, SOURCE, table)
                self.manifest.record("options_eod", u, day, SOURCE, path, table, note=note)
                stats["rows"] += table.num_rows
            stats["days" if raw else "holidays"] += 1
            self.progress(f"bhavcopy {day}: {'ok' if raw else 'holiday'}")
            time.sleep(self.interval)
            day += timedelta(days=1)
        return stats


def parse_empty() -> pa.Table:
    return pa.table({"trade_date": pa.array([], pa.date32())})
