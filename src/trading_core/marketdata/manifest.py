"""``market_data_files`` bookkeeping: what is in the Parquet store and whether it checked out."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
from sqlalchemy import select

from trading_core.clock import IST
from trading_core.db import models as m
from trading_core.db.engine import Database
from trading_core.marketdata.store import contracts_in


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ts_bounds(table: pa.Table) -> tuple[datetime | None, datetime | None]:
    if "ts" not in table.column_names or table.num_rows == 0:
        return None, None
    lo, hi = pc.min(table["ts"]).as_py(), pc.max(table["ts"]).as_py()
    return (lo.replace(tzinfo=IST) if lo else None), (hi.replace(tzinfo=IST) if hi else None)


class Manifest:
    def __init__(self, db: Database):
        self.db = db

    def record(self, dataset: str, key: str, day: date, source: str, path: Path, table: pa.Table,
               status: str = "OK", note: str | None = None, validated_against: str | None = None,
               max_close_diff: float | None = None) -> None:
        first, last = _ts_bounds(table)
        values = dict(path=str(path), rows=table.num_rows, contracts=contracts_in(table), first_ts=first,
                      last_ts=last, sha256=_sha256(path), status=status if table.num_rows else "EMPTY",
                      validated_against=validated_against, max_close_diff=max_close_diff, note=note,
                      written_at=datetime.now(IST))
        with self.db.session() as s:
            row = s.get(m.MarketDataFile, (dataset, key, day, source))
            if row is None:
                s.add(m.MarketDataFile(dataset=dataset, partition_key=key, trade_date=day, source=source, **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)

    def have(self, dataset: str, key: str, day: date, source: str) -> bool:
        with self.db.session() as s:
            row = s.get(m.MarketDataFile, (dataset, key, day, source))
            return row is not None and row.status in ("OK", "EMPTY")

    def dates(self, dataset: str, key: str, source: str | None = None) -> list[date]:
        with self.db.session() as s:
            q = select(m.MarketDataFile.trade_date).where(m.MarketDataFile.dataset == dataset,
                                                         m.MarketDataFile.partition_key == key,
                                                         m.MarketDataFile.status == "OK")
            if source:
                q = q.where(m.MarketDataFile.source == source)
            return sorted(set(s.scalars(q).all()))

    def summary(self) -> list[dict]:
        """Coverage per dataset/key/source: first and last date, days, rows."""
        with self.db.session() as s:
            rows = s.scalars(select(m.MarketDataFile)).all()
        groups: dict[tuple[str, str, str], dict] = {}
        for r in rows:
            g = groups.setdefault((r.dataset, r.partition_key, r.source),
                                  dict(dataset=r.dataset, key=r.partition_key, source=r.source, days=0,
                                       rows=0, first=None, last=None, problems=0))
            if r.status in ("OK", "EMPTY"):
                g["days"] += 1
                g["rows"] += r.rows
                g["first"] = min(filter(None, [g["first"], r.trade_date]))
                g["last"] = max(filter(None, [g["last"], r.trade_date]))
            else:
                g["problems"] += 1
        return sorted(groups.values(), key=lambda g: (g["dataset"], g["key"], g["source"]))
