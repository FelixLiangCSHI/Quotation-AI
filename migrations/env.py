"""Alembic environment.

The database URL comes from the ``DATABASE_URL`` environment variable via
:func:`app.db.session.load_database_settings`. No credentials are stored in
``alembic.ini``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.db.base import Base
from app.db.session import create_database_engine, load_database_settings

# Importing the models registers every table on the shared metadata.
from app.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        return override
    return load_database_settings().url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = load_database_settings()
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        settings = type(settings)(url=override, echo=settings.echo)

    connectable = config.attributes.get("connection", None)
    if connectable is not None:
        _run(connectable)
        return

    engine = create_database_engine(settings)
    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # Batch mode lets SQLite apply ALTER-style migrations later.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
