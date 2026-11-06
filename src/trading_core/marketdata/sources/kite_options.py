"""Real 1-minute option candles from Kite -> ``options_1m``.

Kite serves minute history for live contracts only: a contract keeps its whole history until it
expires, then its token stops working the next day. So missed days can be fetched later, but each
expiry must be saved by its own evening (docs/design/backtest.md, section 5a).

One file per day holds every expiry. The manifest has one row per expiry and day, under the key
``NIFTY:2026-11-09``, so each expiry resumes on its own.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

import pyarrow as pa
import pyarrow.compute as pc
from sqlalchemy import func, select

from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.kite.auth import NotLoggedIn
from trading_core.market.calendar import MarketCalendar
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.sources.kite_index import MINUTE_CHUNK_DAYS, _as_date, _chunks, _naive_ist
from trading_core.marketdata.store import DATASETS, DATE, MarketStore, conform

log = logging.getLogger(__name__)

SOURCE = "kite"
DATASET = "options_1m"
SESSION_START, SESSION_END = time(9, 15), time(15, 30)
DAY_COMPLETE = time(15, 35)       # Kite's candles for the session are final by the report time


def expiry_key(underlying: str, expiry: date) -> str:
    return f"{underlying}:{expiry.isoformat()}"


def last_complete_session(now: datetime, calendar: MarketCalendar) -> date:
    """The latest trading day that has finished: a half-day must never be stored as complete."""
    today = now.date()
    if calendar.is_trading_day(today) and now.time() >= DAY_COMPLETE:
        return today
    return calendar.prev_trading_day(today)


def trading_days(start: date, end: date, calendar: MarketCalendar) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if calendar.is_trading_day(d)]


@dataclass
class ExpiryResult:
    expiry: date
    contracts: int
    stored_days: int = 0         # days with candles for this expiry, in total
    new_days: int = 0
    rows: int = 0
    failed: list[str] = field(default_factory=list)


class KiteOptions:
    def __init__(self, client: Any, store: MarketStore, manifest: Manifest, calendar: MarketCalendar,
                 progress: Callable[[str], None] | None = None):
        self.client, self.store, self.manifest, self.calendar = client, store, manifest, calendar
        self.progress = progress or log.info

    def live_chains(self, underlying: str, today: date) -> dict[date, list[dict[str, Any]]]:
        """Listed option contracts by expiry, nearest first. An expiry today is still live this evening."""
        chains: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for r in self.client.instruments("NFO"):
            if r.get("name") == underlying and r.get("segment") == "NFO-OPT" and r.get("expiry"):
                e = _as_date(r["expiry"])
                if e >= today:
                    chains[e].append(r)
        return dict(sorted(chains.items()))

    def download(self, underlying: str, index_symbol: str, start: date, end: date, today: date,
                 expiries: int = 3, force: bool = False) -> list[ExpiryResult]:
        out = []
        for expiry, contracts in list(self.live_chains(underlying, today).items())[:expiries]:
            out.append(self._expiry(underlying, index_symbol, expiry, contracts, start, end, force))
        return out

    def _expiry(self, underlying: str, index_symbol: str, expiry: date, contracts: list[dict[str, Any]],
                start: date, end: date, force: bool) -> ExpiryResult:
        key = expiry_key(underlying, expiry)
        res = ExpiryResult(expiry, len(contracts))
        days = trading_days(start, min(end, expiry), self.calendar)
        missing = [d for d in days if force or not self.manifest.have(DATASET, key, d, SOURCE)]
        if missing:
            by_day = self._candles(underlying, expiry, contracts, missing[0], missing[-1], res)
            for d in missing:
                self._store_day(underlying, index_symbol, expiry, d, by_day.get(d), res)
        res.stored_days = len(self.manifest.dates(DATASET, key, SOURCE))
        return res

    def _candles(self, underlying: str, expiry: date, contracts: list[dict[str, Any]], lo: date, hi: date,
                 res: ExpiryResult) -> dict[date, dict[str, list]]:
        by_day: dict[date, dict[str, list]] = {}
        for n, c in enumerate(contracts, 1):
            try:
                for a, b in _chunks(lo, hi, MINUTE_CHUNK_DAYS):
                    for k in self.client.historical(int(c["instrument_token"]), datetime.combine(a, SESSION_START),
                                                    datetime.combine(b, SESSION_END), "minute", oi=True):
                        ts = _naive_ist(k["date"])
                        cols = by_day.setdefault(ts.date(), defaultdict(list))
                        for name, v in (("ts", ts), ("strike", float(c["strike"])), ("opt_type", c["instrument_type"]),
                                        ("open", k["open"]), ("high", k["high"]), ("low", k["low"]),
                                        ("close", k["close"]), ("volume", k.get("volume") or 0), ("oi", k.get("oi"))):
                            cols[name].append(v)
            except NotLoggedIn:
                raise
            except Exception as e:  # noqa: BLE001 - one bad contract must not lose the rest of the chain
                log.warning("%s: %s", c.get("tradingsymbol"), e)
                res.failed.append(str(c.get("tradingsymbol")))
            if n % 50 == 0 or n == len(contracts):
                self.progress(f"{underlying} {expiry}: {n}/{len(contracts)} contracts")
        return by_day

    def _store_day(self, underlying: str, index_symbol: str, expiry: date, d: date, cols: dict[str, list] | None,
                   res: ExpiryResult) -> None:
        if cols is None and res.failed:
            return                                     # nothing to keep, and no proof the day was empty
        cols = cols or defaultdict(list)
        n = len(cols["ts"])
        spot = self._spot(index_symbol, d)
        new = conform(pa.table({
            **cols, "trade_date": [d] * n, "underlying": [underlying] * n, "expiry": [expiry] * n,
            "filled": [False] * n, "spot": [spot.get(t) for t in cols["ts"]], "source": [SOURCE] * n,
        }), DATASETS[DATASET].schema)
        old = self.store.read(DATASET, underlying, SOURCE, day=d)
        table = new
        if old is not None:                            # other expiries in the day's file stay as they are
            keep = old.filter(pc.not_equal(old["expiry"], pa.scalar(expiry, type=DATE)))
            table = pa.concat_tables([conform(keep, new.schema), new])
        path, _ = self.store.write_day(DATASET, underlying, d, SOURCE, table)
        self.manifest.record(DATASET, expiry_key(underlying, expiry), d, SOURCE, path, new,
                             status="PARTIAL" if res.failed else "OK",
                             note=f"{len(res.failed)} contracts failed" if res.failed else None)
        res.new_days += 1 if n else 0
        res.rows += n

    def _spot(self, index_symbol: str, d: date) -> dict[datetime, float]:
        t = self.store.read("index_1m", index_symbol, SOURCE, day=d)
        return dict(zip(t["ts"].to_pylist(), t["close"].to_pylist())) if t is not None else {}


def next_expiry(db: Database, underlying: str, today: date) -> date | None:
    """The nearest expiry at or after ``today`` in any stored instrument snapshot."""
    with db.session() as s:
        return s.scalar(select(func.min(m.Instrument.expiry)).where(
            m.Instrument.name == underlying, m.Instrument.segment == "NFO-OPT", m.Instrument.expiry >= today))


def reminder(db: Database, manifest: Manifest, underlying: str, calendar: MarketCalendar,
             now: datetime) -> str | None:
    """What ``algo login`` warns about: an expiry about to be lost, or finished days not stored yet."""
    today = now.date()
    expiry = next_expiry(db, underlying, today)
    if expiry is None:
        return None
    if expiry == today:
        return (f"Today is the {underlying} {expiry} expiry: run `algo data options` this evening. "
                "Kite stops serving these contracts tomorrow.")
    stored = manifest.dates(DATASET, expiry_key(underlying, expiry), SOURCE)
    last = last_complete_session(now, calendar)
    if stored and stored[-1] >= last:
        return None
    pending = trading_days(stored[-1] + timedelta(days=1), last, calendar) if stored else []
    what = f"{len(pending)} finished day(s) not stored yet" if pending else "no days stored yet"
    return f"{underlying} {expiry} options: {what}. Run `algo data options` by the evening of {expiry:%a %d %b}."
