"""Swing watchlists (dashboard Swing › Watchlists): five lists, the first filled by each weekend scan.

"Algo picks" (kind auto) is replaced by every scan with its top picks and their plans; the picks
journal keeps the history (``swing.tracking``). The other four are yours: rename them, add and
remove stocks. Nothing here talks to Kite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from trading_core.db import models as m
from trading_core.db.engine import Database

AUTO_NAME = "Algo picks"
DEFAULTS = [(AUTO_NAME, "auto")] + [(f"Watchlist {i}", "manual") for i in range(2, 6)]
MAX_ITEMS = 100


class WatchlistError(ValueError):
    pass


def ensure(db: Database, now: datetime) -> None:
    """Create the five lists the first time (the migration leaves the table empty)."""
    with db.session() as s:
        if s.scalar(select(func.count()).select_from(m.Watchlist)):
            return
        for i, (name, kind) in enumerate(DEFAULTS):
            s.add(m.Watchlist(name=name, kind=kind, sort=i, created_at=now))


def lists(db: Database, now: datetime) -> list[dict[str, Any]]:
    ensure(db, now)
    with db.session() as s:
        counts = dict(s.execute(select(m.WatchlistItem.watchlist_id, func.count()).group_by(m.WatchlistItem.watchlist_id)).all())
        rows = s.scalars(select(m.Watchlist).order_by(m.Watchlist.sort, m.Watchlist.id)).all()
        return [{"id": w.id, "name": w.name, "kind": w.kind, "count": counts.get(w.id, 0)} for w in rows]


def _get(s, list_id: int) -> m.Watchlist:
    w = s.get(m.Watchlist, list_id)
    if w is None:
        raise LookupError(f"no watchlist {list_id}")
    return w


def items(db: Database, list_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with db.session() as s:
        w = _get(s, list_id)
        rows = s.scalars(select(m.WatchlistItem).where(m.WatchlistItem.watchlist_id == list_id)
                         .order_by(m.WatchlistItem.added_at, m.WatchlistItem.id)).all()
        head = {"id": w.id, "name": w.name, "kind": w.kind}
        return head, [{"symbol": r.symbol, "added_at": r.added_at.isoformat(timespec="seconds"), "note": r.note,
                       "entry": r.entry, "stop": r.stop, "target": r.target, "price_added": r.price_added} for r in rows]


def rename(db: Database, list_id: int, name: str) -> dict[str, Any]:
    name = " ".join(name.split())
    if not name or len(name) > 60:
        raise WatchlistError("a name of 1-60 characters, please")
    with db.session() as s:
        w = _get(s, list_id)
        w.name = name
        return {"id": w.id, "name": w.name, "kind": w.kind}


def add(db: Database, list_id: int, symbol: str, now: datetime, *, note: str | None = None,
        price: float | None = None, entry: float | None = None, stop: float | None = None,
        target: float | None = None) -> bool:
    """Add a stock to a manual list. False if it is already there."""
    with db.session() as s:
        w = _get(s, list_id)
        if w.kind == "auto":
            raise WatchlistError(f"{w.name} is filled by the weekend scan: add the stock to one of your lists")
        if s.scalar(select(m.WatchlistItem.id).where(m.WatchlistItem.watchlist_id == list_id,
                                                     m.WatchlistItem.symbol == symbol)):
            return False
        n = s.scalar(select(func.count()).select_from(m.WatchlistItem).where(m.WatchlistItem.watchlist_id == list_id))
        if n >= MAX_ITEMS:
            raise WatchlistError(f"{w.name} already has {MAX_ITEMS} stocks")
        s.add(m.WatchlistItem(watchlist_id=list_id, symbol=symbol, added_at=now, note=note, price_added=price,
                              entry=entry, stop=stop, target=target))
        return True


def remove(db: Database, list_id: int, symbol: str) -> bool:
    with db.session() as s:
        w = _get(s, list_id)
        if w.kind == "auto":
            raise WatchlistError(f"{w.name} is filled by the weekend scan")
        return s.execute(delete(m.WatchlistItem).where(m.WatchlistItem.watchlist_id == list_id,
                                                       m.WatchlistItem.symbol == symbol)).rowcount > 0


def replace_auto(db: Database, picks: Iterable[dict[str, Any]], now: datetime) -> int:
    """Each scan replaces Algo picks with its top picks (the journal keeps the old ones)."""
    ensure(db, now)
    with db.session() as s:
        w = s.scalars(select(m.Watchlist).where(m.Watchlist.kind == "auto").order_by(m.Watchlist.sort)).first()
        if w is None:
            return 0
        s.execute(delete(m.WatchlistItem).where(m.WatchlistItem.watchlist_id == w.id))
        n = 0
        for p in picks:
            s.add(m.WatchlistItem(watchlist_id=w.id, symbol=p["symbol"], added_at=now, price_added=p["close"],
                                  entry=p["entry"], stop=p["stop"], target=p["target"],
                                  note=f"#{p['rank']} · {p['sector'] or p['industry']} · score {p['score']:.0f}"))
            n += 1
        return n
