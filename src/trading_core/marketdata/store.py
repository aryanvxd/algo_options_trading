"""Parquet market-data store: one file per dataset / key / day / source.

    data/market/<dataset>/<key_name>=<key>/year=<yyyy>/date=<yyyy-mm-dd>/part-<source>.parquet
    data/market/<dataset>/<key_name>=<key>/year=<yyyy>/part-<source>.parquet      (yearly datasets)

Files carry every column themselves (including the key and date), so the directory names are only
for organisation and pruning; DuckDB reads them with ``hive_partitioning=false``.
Writes are atomic: a ``.tmp`` file is written, then renamed over the old one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

F64, F32, I64, I32, STR, BOOL = pa.float64(), pa.float32(), pa.int64(), pa.int32(), pa.string(), pa.bool_()
TS = pa.timestamp("ms")          # naive IST, same convention as the trading DB
DATE = pa.date32()

GREEKS = [("forward", F64), ("tte_years", F64), ("iv", F32), ("delta", F32), ("gamma", F32),
          ("theta", F32), ("vega", F32), ("iv_source", STR)]


@dataclass(frozen=True)
class Dataset:
    name: str
    key_name: str            # partition key: underlying / symbol / universe
    schema: pa.Schema
    sort_by: tuple[str, ...]
    yearly: bool = False     # one file per year instead of per day


def _schema(*fields: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema([pa.field(n, t) for n, t in fields])


DATASETS: dict[str, Dataset] = {d.name: d for d in [
    Dataset("index_1m", "symbol", _schema(
        ("ts", TS), ("trade_date", DATE), ("symbol", STR), ("open", F64), ("high", F64), ("low", F64),
        ("close", F64), ("volume", I64), ("source", STR)), ("ts",)),
    Dataset("options_1m", "underlying", _schema(
        ("ts", TS), ("trade_date", DATE), ("underlying", STR), ("expiry", DATE), ("strike", F64),
        ("opt_type", STR), ("open", F64), ("high", F64), ("low", F64), ("close", F64), ("volume", I64),
        ("oi", I64), ("filled", BOOL), ("spot", F64), *GREEKS, ("source", STR)),
        ("ts", "expiry", "strike", "opt_type")),
    Dataset("options_eod", "underlying", _schema(
        ("trade_date", DATE), ("underlying", STR), ("expiry", DATE), ("strike", F64), ("opt_type", STR),
        ("open", F64), ("high", F64), ("low", F64), ("close", F64), ("settle", F64), ("spot", F64),
        ("oi", I64), ("oi_change", I64), ("contracts", I64), ("lot_size", I32), *GREEKS, ("source", STR)),
        ("expiry", "strike", "opt_type")),
    Dataset("options_ticks", "underlying", _schema(
        ("ts", TS), ("trade_date", DATE), ("instrument_token", I64), ("underlying", STR), ("expiry", DATE),
        ("strike", F64), ("opt_type", STR), ("ltp", F64), ("bid", F64), ("ask", F64), ("bid_qty", I64),
        ("ask_qty", I64), ("oi", I64), ("volume", I64), ("source", STR)),
        ("ts", "expiry", "strike", "opt_type")),
    Dataset("index_ticks", "symbol", _schema(
        ("ts", TS), ("trade_date", DATE), ("instrument_token", I64), ("symbol", STR), ("ltp", F64),
        ("volume", I64), ("source", STR)), ("ts",)),       # volume: futures and stocks (null for an index)
    Dataset("instruments", "scope", _schema(
        ("as_of_date", DATE), ("instrument_token", I64), ("exchange_token", I64), ("tradingsymbol", STR),
        ("name", STR), ("exchange", STR), ("segment", STR), ("instrument_type", STR), ("strike", F64),
        ("expiry", DATE), ("lot_size", I32), ("tick_size", F64), ("source", STR)),
        ("instrument_token",)),
    Dataset("equity_daily", "universe", _schema(
        ("trade_date", DATE), ("symbol", STR), ("open", F64), ("high", F64), ("low", F64), ("close", F64),
        ("volume", I64), ("source", STR)), ("trade_date", "symbol"), yearly=True),
]}


def slug(value: str) -> str:
    """Directory-safe partition value: 'NIFTY 50' -> 'NIFTY_50'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())


def conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Select/cast columns to ``schema``; missing columns become nulls, extra ones are dropped."""
    cols = []
    for f in schema:
        if f.name in table.column_names:
            cols.append(table[f.name].cast(f.type, safe=False))
        else:
            cols.append(pa.nulls(table.num_rows, type=f.type))
    return pa.Table.from_arrays(cols, schema=schema)


class MarketStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def dataset(self, name: str) -> Dataset:
        return DATASETS[name]

    def path(self, dataset: str, key: str, source: str, day: date | None = None, year: int | None = None) -> Path:
        ds = DATASETS[dataset]
        base = self.root / ds.name / f"{ds.key_name}={slug(key)}"
        if ds.yearly:
            return base / f"year={year or day.year}" / f"part-{slug(source)}.parquet"
        return base / f"year={day.year}" / f"date={day.isoformat()}" / f"part-{slug(source)}.parquet"

    def glob(self, dataset: str) -> str:
        ds = DATASETS[dataset]
        depth = "*/*/*.parquet" if ds.yearly else "*/*/*/*.parquet"
        return (self.root / ds.name).as_posix() + "/" + depth

    def has_files(self, dataset: str) -> bool:
        return any((self.root / dataset).rglob("*.parquet")) if (self.root / dataset).exists() else False

    def _write(self, path: Path, table: pa.Table) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd", compression_level=6)
        os.replace(tmp, path)
        return path

    def _prepare(self, dataset: str, table: pa.Table) -> pa.Table:
        ds = DATASETS[dataset]
        table = conform(table, ds.schema)
        if table.num_rows:
            table = table.sort_by([(c, "ascending") for c in ds.sort_by])
        return table

    def write_day(self, dataset: str, key: str, day: date, source: str, table: pa.Table) -> tuple[Path, pa.Table]:
        table = self._prepare(dataset, table)
        return self._write(self.path(dataset, key, source, day=day), table), table

    def write_year(self, dataset: str, key: str, year: int, source: str, table: pa.Table,
                   unique_on: tuple[str, ...]) -> tuple[Path, pa.Table]:
        """Merge ``table`` into that year's file; rows in ``table`` win on ``unique_on`` clashes."""
        path = self.path(dataset, key, source, year=year)
        new = self._prepare(dataset, table)
        if path.exists():
            old = conform(pq.read_table(path), DATASETS[dataset].schema)
            keys = set(zip(*(new[c].to_pylist() for c in unique_on)))
            keep = [tuple(r) not in keys for r in zip(*(old[c].to_pylist() for c in unique_on))]
            new = pa.concat_tables([old.filter(pa.array(keep, type=pa.bool_())), new])
        return self._write(path, self._prepare(dataset, new)), new

    def read(self, dataset: str, key: str, source: str, day: date | None = None,
             year: int | None = None) -> pa.Table | None:
        path = self.path(dataset, key, source, day=day, year=year)
        return pq.read_table(path) if path.exists() else None

    def days(self, dataset: str, key: str, source: str | None = None) -> list[date]:
        ds = DATASETS[dataset]
        base = self.root / ds.name / f"{ds.key_name}={slug(key)}"
        pattern = f"*/date=*/part-{slug(source)}.parquet" if source else "*/date=*/*.parquet"
        found = {date.fromisoformat(p.parent.name.split("=", 1)[1]) for p in base.glob(pattern)}
        return sorted(found)


def contracts_in(table: pa.Table) -> int | None:
    if not {"expiry", "strike", "opt_type"} <= set(table.column_names) or table.num_rows == 0:
        return None
    return pc.count_distinct(pc.binary_join_element_wise(
        pc.cast(table["expiry"], STR), pc.cast(table["strike"], STR), table["opt_type"], "|")).as_py()
