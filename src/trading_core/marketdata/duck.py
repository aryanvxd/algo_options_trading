"""DuckDB over the Parquet store: one view per dataset, queried in place (nothing is loaded up front).

    mdb = MarketDB(MarketStore(platform.marketdata.dir))
    mdb.sql("select * from options_eod where trade_date = ? and underlying = 'NIFTY'", [day])
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import duckdb
import pyarrow as pa

from trading_core.marketdata.store import DATASETS, MarketStore


class MarketDB:
    def __init__(self, store: MarketStore, threads: int | None = None):
        self.store = store
        self.con = duckdb.connect(":memory:")
        if threads:
            self.con.execute(f"SET threads = {int(threads)}")
        self.refresh()

    def refresh(self) -> None:
        """(Re)create views; call after new files were written in this process."""
        for name, ds in DATASETS.items():
            if self.store.has_files(name):
                glob = self.store.glob(name).replace("'", "''")
                self.con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{glob}', "
                                 "union_by_name = true, hive_partitioning = false)")
            else:
                empty = ds.schema.empty_table()
                self.con.register(f"_{name}_empty", empty)
                self.con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM _{name}_empty")

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pa.Table:
        rel = self.con.sql(query, params=list(params) if params else None)
        # DuckDB 2.0 renamed Relation.arrow() to to_arrow_table()
        return rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.arrow()

    def rows(self, query: str, params: Sequence[Any] | None = None) -> list[tuple]:
        return self.con.execute(query, params or []).fetchall()

    # ------------------------------------------------------------ common questions

    def index_day(self, symbol: str, day: date) -> pa.Table:
        return self.sql("SELECT * FROM index_1m WHERE symbol = ? AND trade_date = ? ORDER BY ts", [symbol, day])

    def eod_chain(self, underlying: str, day: date, expiry: date | None = None) -> pa.Table:
        if expiry is None:
            expiry = self.rows("SELECT min(expiry) FROM options_eod WHERE underlying = ? AND trade_date = ? "
                               "AND expiry >= trade_date", [underlying, day])[0][0]
        return self.sql("SELECT * FROM options_eod WHERE underlying = ? AND trade_date = ? AND expiry = ? "
                        "ORDER BY strike, opt_type", [underlying, day, expiry])

    def expiries(self, underlying: str) -> list[tuple[date, int | None]]:
        """Every expiry seen in the EOD data with its lot size (from UDiFF files)."""
        return self.rows("SELECT expiry, max(lot_size) FROM options_eod WHERE underlying = ? "
                         "GROUP BY expiry ORDER BY expiry", [underlying])

    def close(self) -> None:
        self.con.close()
