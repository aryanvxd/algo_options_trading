"""Daily instrument snapshots and lookups (expiries, option chains, index tokens).

Only what the platform needs is stored: option chains and futures for ``instruments.nfo_underlyings``
and the NSE index segment. Each day is kept under its own ``as_of_date`` so replays of past days resolve
the same tokens and strikes the live run saw.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, insert, select

from trading_core.config import InstrumentsConfig
from trading_core.db import models as m
from trading_core.db.engine import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstrumentInfo:
    instrument_token: int
    tradingsymbol: str
    name: str | None
    exchange: str
    segment: str
    instrument_type: str
    strike: float | None
    expiry: date | None
    lot_size: int
    tick_size: float

    @property
    def quote_key(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"


def _to_info(r: m.Instrument) -> InstrumentInfo:
    return InstrumentInfo(r.instrument_token, r.tradingsymbol, r.name, r.exchange, r.segment, r.instrument_type,
                          r.strike, r.expiry, r.lot_size, r.tick_size)


def filter_rows(nfo: Iterable[dict[str, Any]], nse: Iterable[dict[str, Any]],
                underlyings: list[str], equities: Iterable[str] = ()) -> list[dict[str, Any]]:
    wanted, stocks = set(underlyings), set(equities)
    out = [r for r in nfo if r.get("segment") in ("NFO-OPT", "NFO-FUT") and r.get("name") in wanted]
    nse = list(nse)
    out += [r for r in nse if r.get("segment") == "INDICES"]
    out += [r for r in nse if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"
            and r.get("tradingsymbol") in stocks]
    return out


class InstrumentStore:
    def __init__(self, db: Database, cfg: InstrumentsConfig):
        self.db = db
        self.cfg = cfg

    def has_snapshot(self, as_of: date) -> bool:
        with self.db.session() as s:
            return bool(s.scalar(select(func.count()).select_from(m.Instrument)
                                 .where(m.Instrument.as_of_date == as_of)))

    def save_snapshot(self, as_of: date, rows: Iterable[dict[str, Any]]) -> int:
        records = []
        for r in rows:
            expiry = r.get("expiry") or None
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry) if expiry else None
            records.append(dict(
                as_of_date=as_of, instrument_token=int(r["instrument_token"]),
                exchange_token=int(r["exchange_token"]) if r.get("exchange_token") else None,
                tradingsymbol=r["tradingsymbol"], name=r.get("name"), exchange=r["exchange"],
                segment=r["segment"], instrument_type=r["instrument_type"],
                strike=float(r["strike"]) if r.get("strike") not in (None, "") else None,
                expiry=expiry, lot_size=int(r.get("lot_size") or 1), tick_size=float(r.get("tick_size") or 0.05),
            ))
        with self.db.session() as s:
            s.execute(delete(m.Instrument).where(m.Instrument.as_of_date == as_of))
            if records:
                s.execute(insert(m.Instrument), records)
            cutoff = as_of - timedelta(days=self.cfg.keep_days)
            s.execute(delete(m.Instrument).where(m.Instrument.as_of_date < cutoff))
        return len(records)

    def refresh_from_kite(self, client: Any, as_of: date, equities: Iterable[str] = ()) -> int:
        rows = filter_rows(client.instruments("NFO"), client.instruments("NSE"), self.cfg.nfo_underlyings, equities)
        n = self.save_snapshot(as_of, rows)
        log.info("instrument snapshot %s: %d rows", as_of, n)
        return n

    def nearest_expiry(self, underlying: str, as_of: date) -> date | None:
        with self.db.session() as s:
            return s.scalar(select(func.min(m.Instrument.expiry)).where(
                m.Instrument.as_of_date == as_of, m.Instrument.name == underlying,
                m.Instrument.segment == "NFO-OPT", m.Instrument.expiry >= as_of))

    def nearest_future(self, underlying: str, as_of: date) -> InstrumentInfo | None:
        """The near-month future (None for snapshots taken before futures were stored)."""
        with self.db.session() as s:
            r = s.scalars(select(m.Instrument).where(
                m.Instrument.as_of_date == as_of, m.Instrument.name == underlying,
                m.Instrument.segment == "NFO-FUT", m.Instrument.expiry >= as_of,
            ).order_by(m.Instrument.expiry)).first()
            return _to_info(r) if r else None

    def option_chain(self, underlying: str, expiry: date, as_of: date) -> list[InstrumentInfo]:
        with self.db.session() as s:
            rows = s.scalars(select(m.Instrument).where(
                m.Instrument.as_of_date == as_of, m.Instrument.name == underlying,
                m.Instrument.segment == "NFO-OPT", m.Instrument.expiry == expiry,
            ).order_by(m.Instrument.strike)).all()
            return [_to_info(r) for r in rows]

    def by_symbol(self, exchange: str, tradingsymbol: str, as_of: date) -> InstrumentInfo | None:
        with self.db.session() as s:
            r = s.scalars(select(m.Instrument).where(
                m.Instrument.as_of_date == as_of, m.Instrument.exchange == exchange,
                m.Instrument.tradingsymbol == tradingsymbol)).first()
            return _to_info(r) if r else None

    def equities(self, symbols: Iterable[str], as_of: date) -> dict[str, InstrumentInfo]:
        """NSE cash stocks in the snapshot, by symbol (missing ones are simply absent)."""
        symbols = list(symbols)
        with self.db.session() as s:
            rows = s.scalars(select(m.Instrument).where(
                m.Instrument.as_of_date == as_of, m.Instrument.segment == "NSE",
                m.Instrument.tradingsymbol.in_(symbols))).all()
            return {r.tradingsymbol: _to_info(r) for r in rows}

    def by_tokens(self, tokens: Iterable[int], as_of: date) -> dict[int, InstrumentInfo]:
        tokens = list(tokens)
        with self.db.session() as s:
            rows = s.scalars(select(m.Instrument).where(
                m.Instrument.as_of_date == as_of, m.Instrument.instrument_token.in_(tokens))).all()
            return {r.instrument_token: _to_info(r) for r in rows}
