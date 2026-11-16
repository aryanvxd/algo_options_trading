"""Fundamentals for the swing ranking: numbers per stock (ROCE, P/B, debt to equity, ...).

There is no automatic source yet (Kite has none): you upload a CSV on Swing › Fundamentals, for
example a Screener export, and choose the columns to keep. Each column becomes a metric key, lower
case with words joined by ``_`` ("ROCE %" -> ``roce``, "Price to book value" -> ``price_to_book_value``).
An automatic source can later write to the same table with its own ``source``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from swing.settings import FundRule
from trading_core.db import models as m
from trading_core.db.engine import Database

MAX_ROWS = 5000


def metric_key(column: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", column.lower().replace("%", " ")).strip("_")
    return key[:60]


def save(db: Database, rows: Iterable[dict[str, Any]], as_of: date | None, now: datetime,
         source: str = "csv") -> dict[str, int]:
    """``rows``: [{symbol, metrics: {column: value}}]. Replaces those stocks' values of those metrics."""
    rows = list(rows)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"at most {MAX_ROWS} rows")
    values: dict[tuple[str, str], float] = {}
    for r in rows:
        sym = str(r.get("symbol", "")).strip().upper()
        if not sym:
            continue
        for col, v in (r.get("metrics") or {}).items():
            if v is None or v == "":
                continue
            try:
                values[(sym, metric_key(col))] = float(v)
            except (TypeError, ValueError):
                continue
    with db.session() as s:
        for (sym, key), v in values.items():
            s.execute(delete(m.Fundamental).where(m.Fundamental.symbol == sym, m.Fundamental.metric == key))
            s.add(m.Fundamental(symbol=sym, metric=key, value=v, as_of=as_of, source=source, updated_at=now))
    return {"stocks": len({k[0] for k in values}), "values": len(values)}


def clear(db: Database) -> int:
    with db.session() as s:
        return s.execute(delete(m.Fundamental)).rowcount


def of(db: Database, symbols: Iterable[str] | None = None) -> dict[str, dict[str, float]]:
    q = select(m.Fundamental.symbol, m.Fundamental.metric, m.Fundamental.value)
    if symbols is not None:
        q = q.where(m.Fundamental.symbol.in_(list(symbols)))
    out: dict[str, dict[str, float]] = {}
    with db.session() as s:
        for sym, key, v in s.execute(q):
            out.setdefault(sym, {})[key] = v
    return out


def summary(db: Database) -> dict[str, Any]:
    with db.session() as s:
        per = s.execute(select(m.Fundamental.metric, func.count(), func.max(m.Fundamental.as_of),
                               func.max(m.Fundamental.updated_at)).group_by(m.Fundamental.metric)
                        .order_by(m.Fundamental.metric)).all()
        stocks = s.scalar(select(func.count(func.distinct(m.Fundamental.symbol))))
    return {"stocks": stocks or 0,
            "metrics": [{"metric": k, "stocks": n, "as_of": a.isoformat() if a else None,
                         "updated_at": u.isoformat(timespec="seconds") if u else None} for k, n, a, u in per]}


def score(values: dict[str, float] | None, rules: list[FundRule]) -> tuple[float | None, list[dict[str, Any]]]:
    """Share of the rules a stock passes, 0-100, over the rules it has numbers for. None: no numbers."""
    if not values:
        return None, []
    checks = []
    for r in rules:
        v = values.get(r.metric)
        if v is None:
            continue
        ok = v >= r.value if r.op == ">=" else v <= r.value
        checks.append({"metric": r.metric, "op": r.op, "value": r.value, "actual": v, "ok": ok})
    if not checks:
        return None, []
    return round(100 * sum(c["ok"] for c in checks) / len(checks), 1), checks
