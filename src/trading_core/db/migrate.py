"""Programmatic Alembic upgrade + reference-data seeding (used by ``algo db upgrade``)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from trading_core.config import PlatformConfig, read_yaml
from trading_core.db import models as m
from trading_core.db.engine import Database


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)


def seed_holidays(db: Database, platform: PlatformConfig) -> int:
    """Insert/refresh holidays from config/holidays/*.yaml. Returns number of rows written."""
    n = 0
    for path in sorted((platform.config_dir / "holidays").glob("*.yaml")):
        data = read_yaml(path)
        exchange = data.get("exchange", "NSE")
        with db.session() as s:
            for h in data.get("holidays", []):
                d = h["date"] if isinstance(h["date"], date) else date.fromisoformat(str(h["date"]))
                row = s.get(m.MarketHoliday, (d, exchange))
                if row is None:
                    s.add(m.MarketHoliday(date=d, exchange=exchange, description=h.get("description")))
                else:
                    row.description = h.get("description")
                n += 1
    return n


def holidays(db: Database, exchange: str = "NSE") -> list[date]:
    with db.session() as s:
        return list(s.scalars(select(m.MarketHoliday.date).where(m.MarketHoliday.exchange == exchange)).all())
