from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_core.db.models import Base


class Database:
    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(url)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session inside a transaction: commits on success, rolls back on error."""
        with self._factory() as s, s.begin():
            yield s

    def create_all(self) -> None:
        """Create tables directly (tests). Real DBs use ``algo db upgrade`` (Alembic)."""
        Base.metadata.create_all(self.engine)


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    # WAL lets the dashboard read while an algo process writes; busy_timeout absorbs short lock waits.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
