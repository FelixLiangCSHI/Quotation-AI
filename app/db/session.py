"""Database engine and session factory.

The database URL is read from configuration only. No credentials appear in
source. PostgreSQL is the internal MVP target; SQLite is supported for local
development and automated tests.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./quotation_ai.db"


@dataclass(frozen=True)
class DatabaseSettings:
    url: str = DEFAULT_DATABASE_URL
    echo: bool = False

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")


def load_database_settings(
    environment: Mapping[str, str] | None = None,
) -> DatabaseSettings:
    values = os.environ if environment is None else environment
    url = (values.get("DATABASE_URL") or "").strip() or DEFAULT_DATABASE_URL
    echo = (values.get("DATABASE_ECHO") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return DatabaseSettings(url=url, echo=echo)


def create_database_engine(settings: DatabaseSettings | None = None) -> Engine:
    resolved = settings or load_database_settings()
    kwargs: dict[str, object] = {"echo": resolved.echo, "future": True}
    if resolved.is_sqlite:
        # Required so an in-memory test database is shared across sessions.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in resolved.url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True

    engine = create_engine(resolved.url, **kwargs)

    if resolved.is_sqlite:
        # SQLite ignores foreign keys unless explicitly enabled, which would
        # hide referential errors that PostgreSQL would raise.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_database_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_engine())


def reset_engine_cache() -> None:
    """Clear cached engines. Used by tests that swap the database URL."""

    get_engine.cache_clear()
    get_session_factory.cache_clear()
