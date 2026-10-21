from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from trading_core.config import load_platform_config
from trading_core.db.models import Base, ISTDateTime

config = context.config
target_metadata = Base.metadata

url = config.get_main_option("sqlalchemy.url") or load_platform_config().db_url


def render_item(type_, obj, autogen_context):
    # ISTDateTime is stored as a plain DateTime; keep migrations free of app imports.
    if type_ == "type" and isinstance(obj, ISTDateTime):
        return "sa.DateTime()"
    return False


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True, render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
