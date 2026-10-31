"""1-minute history from Kite: indices (NIFTY 50, INDIA VIX), the continuous NIFTY future and the
index's stocks; plus the stocks' daily bars.

These are live instruments, so Kite's historical API serves them (free with Kite Connect).
Minute requests are limited to 60 days each; daily to 2000 days.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pyarrow as pa

from trading_core.clock import IST
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)

# Index tokens never change; the dump is only consulted for anything else.
KNOWN_INDEX_TOKENS = {"NIFTY 50": 256265, "INDIA VIX": 264969, "NIFTY BANK": 260105}
MINUTE_CHUNK_DAYS = 60
DAILY_CHUNK_DAYS = 2000
SOURCE = "kite"


def _naive_ist(ts: datetime) -> datetime:
    return ts.astimezone(IST).replace(tzinfo=None) if ts.tzinfo else ts


def _chunks(start: date, end: date, days: int):
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=days - 1))
        yield cur, stop
        cur = stop + timedelta(days=1)


def _as_date(v: Any) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _weekdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


class KiteHistory:
    def __init__(self, client: Any, store: MarketStore, manifest: Manifest,
                 progress: Callable[[str], None] | None = None):
        self.client = client
        self.store = store
        self.manifest = manifest
        self.progress = progress or log.info
        self._nse_dump: list[dict[str, Any]] | None = None

    def _nse(self) -> list[dict[str, Any]]:
        if self._nse_dump is None:
            self._nse_dump = self.client.instruments("NSE")
        return self._nse_dump

    def token_for(self, symbol: str, segment: str = "INDICES") -> int:
        if segment == "INDICES" and symbol in KNOWN_INDEX_TOKENS:
            return KNOWN_INDEX_TOKENS[symbol]
        for row in self._nse():
            if row.get("tradingsymbol") == symbol and row.get("segment") == segment:
                return int(row["instrument_token"])
        raise KeyError(f"{symbol} not found in Kite's NSE {segment} instruments")

    def index_1m(self, symbol: str, start: date, end: date, force: bool = False) -> dict[str, int]:
        """Download 1-min bars for ``symbol``; one Parquet file per trading day. Weekdays with no
        data (holidays) are recorded as EMPTY so they are not requested again."""
        return self.minute("index_1m", symbol, self.token_for(symbol), start, end, force)

    def future_token(self, underlying: str) -> int:
        """The current near-month future."""
        today = date.today()
        futs = [r for r in self.client.instruments("NFO") if r.get("segment") == "NFO-FUT"
                and r.get("name") == underlying and r.get("expiry") and _as_date(r["expiry"]) >= today]
        if not futs:
            raise KeyError(f"no live {underlying} future in Kite's NFO instruments")
        return int(min(futs, key=lambda r: _as_date(r["expiry"]))["instrument_token"])

    def futures_1m(self, underlying: str, start: date, end: date, force: bool = False) -> dict[str, int]:
        """The current near-month future, 1-min with volume, stored as index_1m '<UNDERLYING> FUT'.
        Kite serves minute candles only for live contracts (``continuous`` is daily-only), so this
        reaches back to the contract's listing, about 3 months."""
        return self.minute("index_1m", f"{underlying} FUT", self.future_token(underlying), start, end, force)

    def equity_1m(self, symbols: list[str], start: date, end: date, force: bool = False) -> dict[str, int]:
        """1-min bars of each stock (the backtest's breadth). Symbols Kite doesn't list are skipped."""
        total = dict(days=0, rows=0, skipped=0, empty=0, missing=0)
        for sym in symbols:
            try:
                token = self.token_for(sym, segment="NSE")
            except KeyError:
                total["missing"] += 1
                log.warning("%s not found on Kite (renamed/delisted?)", sym)
                continue
            for k, v in self.minute("equity_1m", sym, token, start, end, force).items():
                total[k] += v
        return total

    def minute(self, dataset: str, key: str, token: int, start: date, end: date, force: bool = False,
               continuous: bool = False) -> dict[str, int]:
        symbol = key
        stats = dict(days=0, rows=0, skipped=0, empty=0)
        for lo, hi in _chunks(start, end, MINUTE_CHUNK_DAYS):
            wanted = _weekdays(lo, hi)
            if not force and all(self.manifest.have(dataset, symbol, d, SOURCE) for d in wanted):
                stats["skipped"] += len(wanted)
                continue
            candles = self.client.historical(token, datetime.combine(lo, datetime.min.time()),
                                             datetime.combine(hi, datetime.max.time().replace(microsecond=0)),
                                             "minute", continuous=continuous)
            by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
            for c in candles:
                ts = _naive_ist(c["date"])
                by_day[ts.date()].append(c | {"ts": ts})
            for d in wanted:
                rows = by_day.get(d, [])
                if not force and self.manifest.have(dataset, symbol, d, SOURCE):
                    stats["skipped"] += 1
                    continue
                table = pa.table({
                    "ts": [r["ts"] for r in rows], "trade_date": [d] * len(rows), "symbol": [symbol] * len(rows),
                    "open": [r["open"] for r in rows], "high": [r["high"] for r in rows],
                    "low": [r["low"] for r in rows], "close": [r["close"] for r in rows],
                    "volume": [r.get("volume") or 0 for r in rows], "source": [SOURCE] * len(rows)})
                path, table = self.store.write_day(dataset, symbol, d, SOURCE, table)
                self.manifest.record(dataset, symbol, d, SOURCE, path, table,
                                     note=None if rows else "no candles (holiday?)")
                stats["days" if rows else "empty"] += 1
                stats["rows"] += len(rows)
            self.progress(f"{symbol} {lo}..{hi}: {len(candles)} candles")
        return stats

    def equity_daily(self, universe: str, symbols: list[str], start: date, end: date) -> dict[str, int]:
        """Daily bars for index constituents (for the advance/decline filter in backtests).
        Note: uses today's constituent list, so older years carry some survivorship bias."""
        by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
        missing = []
        for sym in symbols:
            try:
                token = self.token_for(sym, segment="NSE")
            except KeyError:
                missing.append(sym)
                continue
            for lo, hi in _chunks(start, end, DAILY_CHUNK_DAYS):
                for c in self.client.historical(token, lo, hi, "day"):
                    d = _naive_ist(c["date"]).date() if isinstance(c["date"], datetime) else c["date"]
                    by_year[d.year].append(dict(trade_date=d, symbol=sym, open=c["open"], high=c["high"],
                                                low=c["low"], close=c["close"], volume=c.get("volume") or 0,
                                                source=SOURCE))
            self.progress(f"{universe}: {sym} done")
        rows_total = 0
        for year, rows in sorted(by_year.items()):
            table = pa.Table.from_pylist(rows)
            path, merged = self.store.write_year("equity_daily", universe, year, SOURCE, table,
                                                 unique_on=("trade_date", "symbol"))
            self.manifest.record("equity_daily", universe, date(year, 1, 1), SOURCE, path, merged,
                                 note=f"{len(symbols) - len(missing)} symbols")
            rows_total += len(rows)
        if missing:
            log.warning("not found on Kite (renamed/delisted?): %s", ", ".join(missing))
        return dict(years=len(by_year), rows=rows_total, missing=len(missing))
