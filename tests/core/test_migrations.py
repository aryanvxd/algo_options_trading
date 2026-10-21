from sqlalchemy import create_engine, inspect

from trading_core.db import migrate
from trading_core.db.models import Base


def test_alembic_head_matches_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    migrate.upgrade(url)
    insp = inspect(create_engine(url))
    tables = set(insp.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name

    migrate.downgrade(url, "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}


def test_holidays_seeded(platform, db):
    n = migrate.seed_holidays(db, platform)
    assert n > 0 and len(migrate.holidays(db)) == n
    assert migrate.seed_holidays(db, platform) == n          # idempotent
