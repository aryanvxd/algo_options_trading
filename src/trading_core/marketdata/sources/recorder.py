"""Our own recorded data (Kite WebSocket, 5-second snapshots with bid/ask) -> Parquet.

Runs at the end of every live session (and on demand with ``algo data export-recorder``):
  tick_snapshots (SQLite)  -> options_ticks / index_ticks (Parquet, per underlying / symbol)
  instruments (SQLite)     -> instruments (Parquet), so replays still work after SQLite prunes it
Once a day is exported, its ticks are deleted from SQLite after ``sqlite_tick_keep_days``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import delete, select

from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.kite.instruments import InstrumentStore
from trading_core.marketdata.manifest import Manifest
from trading_core.marketdata.store import MarketStore

log = logging.getLogger(__name__)

SOURCE = "recorder"
INSTRUMENTS_KEY = "all"


def _naive(ts):
    return ts.replace(tzinfo=None) if ts is not None and ts.tzinfo else ts


def export_day(db: Database, store: MarketStore, manifest: Manifest, day: date) -> dict[str, int]:
    with db.session() as s:
        rows = s.execute(
            select(m.TickSnapshot).join(m.AlgoRun, m.AlgoRun.run_id == m.TickSnapshot.run_id)
            .where(m.AlgoRun.trade_date == day, m.AlgoRun.source == "live")
            .order_by(m.TickSnapshot.ts)
        ).scalars().all()
        inst_rows = s.scalars(select(m.Instrument).where(m.Instrument.as_of_date == day)).all()
    if not rows and not inst_rows:
        return dict(ticks=0, options=0, index=0, unknown=0)

    info = {r.instrument_token: r for r in inst_rows}
    seen: set[tuple] = set()
    options: dict[str, list[dict]] = defaultdict(list)
    index: dict[str, list[dict]] = defaultdict(list)
    unknown = 0
    for t in rows:
        key = (t.ts, t.instrument_token)
        if key in seen:           # several algos may record the same instrument
            continue
        seen.add(key)
        inst = info.get(t.instrument_token)
        if inst is None:
            unknown += 1
        elif inst.segment == "NFO-OPT":
            options[inst.name].append(dict(
                ts=_naive(t.ts), trade_date=day, instrument_token=t.instrument_token, underlying=inst.name,
                expiry=inst.expiry, strike=inst.strike, opt_type=inst.instrument_type, ltp=t.ltp, bid=t.bid,
                ask=t.ask, bid_qty=t.bid_qty, ask_qty=t.ask_qty, oi=t.oi, volume=t.volume, source=SOURCE))
        elif inst.segment in ("INDICES", "NFO-FUT"):     # futures: last price only, like an index
            index[inst.tradingsymbol].append(dict(ts=_naive(t.ts), trade_date=day,
                                                  instrument_token=t.instrument_token,
                                                  symbol=inst.tradingsymbol, ltp=t.ltp, source=SOURCE))
        else:
            unknown += 1

    for underlying, recs in options.items():
        path, table = store.write_day("options_ticks", underlying, day, SOURCE, pa.Table.from_pylist(recs))
        manifest.record("options_ticks", underlying, day, SOURCE, path, table)
    for symbol, recs in index.items():
        path, table = store.write_day("index_ticks", symbol, day, SOURCE, pa.Table.from_pylist(recs))
        manifest.record("index_ticks", symbol, day, SOURCE, path, table)
    if inst_rows:
        table = pa.Table.from_pylist([dict(
            as_of_date=r.as_of_date, instrument_token=r.instrument_token, exchange_token=r.exchange_token,
            tradingsymbol=r.tradingsymbol, name=r.name, exchange=r.exchange, segment=r.segment,
            instrument_type=r.instrument_type, strike=r.strike, expiry=r.expiry, lot_size=r.lot_size,
            tick_size=r.tick_size, source=SOURCE) for r in inst_rows])
        path, table = store.write_day("instruments", INSTRUMENTS_KEY, day, SOURCE, table)
        manifest.record("instruments", INSTRUMENTS_KEY, day, SOURCE, path, table,
                        note=f"{sum(len(v) for v in options.values())} option ticks, "
                             f"{sum(len(v) for v in index.values())} index ticks")
    if unknown:
        log.warning("recorder export %s: %d ticks for tokens missing from the instrument snapshot", day, unknown)
    return dict(ticks=len(seen), options=sum(len(v) for v in options.values()),
                index=sum(len(v) for v in index.values()), unknown=unknown)


def prune_sqlite_ticks(db: Database, manifest: Manifest, today: date, keep_days: int) -> int:
    """Delete recorded ticks older than ``keep_days`` from SQLite, but only for days already exported."""
    cutoff = today - timedelta(days=keep_days)
    with db.session() as s:
        runs = s.execute(select(m.AlgoRun.run_id, m.AlgoRun.trade_date)
                         .where(m.AlgoRun.trade_date < cutoff, m.AlgoRun.source == "live")).all()
    exported = {d for d in {d for _, d in runs} if manifest.have("instruments", INSTRUMENTS_KEY, d, SOURCE)}
    run_ids = [rid for rid, d in runs if d in exported]
    if not run_ids:
        return 0
    with db.session() as s:
        res = s.execute(delete(m.TickSnapshot).where(m.TickSnapshot.run_id.in_(run_ids)))
    return res.rowcount or 0


def restore_instruments(store: MarketStore, instruments: InstrumentStore, day: date) -> bool:
    """Put an exported instrument snapshot back into SQLite (replaying a day older than its retention)."""
    table = store.read("instruments", INSTRUMENTS_KEY, SOURCE, day=day)
    if table is None or table.num_rows == 0:
        return False
    instruments.save_snapshot(day, table.to_pylist())
    return True


def read_ticks(store: MarketStore, day: date) -> list[pa.Table]:
    """All exported option and index tick tables for ``day`` (any underlying/symbol)."""
    out = []
    for dataset in ("options_ticks", "index_ticks"):
        base = store.root / dataset
        for path in base.glob(f"*/year={day.year}/date={day.isoformat()}/part-{SOURCE}.parquet"):
            out.append(pq.read_table(path, columns=["ts", "instrument_token", "ltp"]
                                     + (["bid", "ask", "bid_qty", "ask_qty", "oi", "volume"]
                                        if dataset == "options_ticks" else [])))
    return out
