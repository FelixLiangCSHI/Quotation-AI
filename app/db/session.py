"""Database engine and session factory.

The database URL is read from configuration only. No credentials appear in
source. PostgreSQL is the internal MVP target; SQLite is supported for local
development and automated tests.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./quotation_ai.db"
MEMORY_DATABASE_URL = "sqlite+pysqlite:///:memory:"

#: Demo-safe storage modes. Streamlit Community Cloud offers no guaranteed
#: persistent local storage, so the demo must be able to run entirely on
#: throwaway storage.
DEMO_DATABASE_MODES = frozenset({"auto", "local_file", "temporary_file", "memory"})
DEFAULT_DEMO_DATABASE_MODE = "auto"


def _demo_database_mode(environment: Mapping[str, str]) -> str:
    mode = (
        (environment.get("DEMO_DATABASE_MODE") or "").strip().casefold()
        or DEFAULT_DEMO_DATABASE_MODE
    )
    return mode if mode in DEMO_DATABASE_MODES else DEFAULT_DEMO_DATABASE_MODE


def _temporary_database_url() -> str:
    """A per-process SQLite file inside the OS temporary directory."""

    path = Path(tempfile.gettempdir()) / "quotation_ai_demo.db"
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _local_file_is_writable() -> bool:
    try:
        probe = Path("./.quotation_ai_write_probe")
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def resolve_demo_database_url(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the SQLite URL used when ``DATABASE_URL`` is not configured."""

    values = os.environ if environment is None else environment
    mode = _demo_database_mode(values)
    if mode == "memory":
        return MEMORY_DATABASE_URL
    if mode == "temporary_file":
        return _temporary_database_url()
    if mode == "local_file":
        return DEFAULT_DATABASE_URL
    return (
        DEFAULT_DATABASE_URL
        if _local_file_is_writable()
        else _temporary_database_url()
    )


def describe_database_mode(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Secret-free description of the active storage mode.

    The returned ``target`` never contains a credential: an external database
    is reported by driver only.
    """

    values = os.environ if environment is None else environment
    configured = (values.get("DATABASE_URL") or "").strip()
    settings = load_database_settings(values)
    if configured and not settings.is_sqlite:
        driver = settings.url.split("://", 1)[0]
        return {
            "mode": "configured external database",
            "target": driver,
            "persistence": "managed by the configured database",
        }
    if ":memory:" in settings.url:
        return {
            "mode": "demo (in-memory SQLite)",
            "target": "sqlite in-memory",
            "persistence": "discarded when the process restarts",
        }
    if configured:
        return {
            "mode": "configured SQLite file",
            "target": "sqlite file",
            "persistence": "depends on the host filesystem",
        }
    temporary = settings.url == _temporary_database_url()
    return {
        "mode": (
            "demo (temporary SQLite file)"
            if temporary
            else "demo (local SQLite file)"
        ),
        "target": "sqlite file",
        "persistence": (
            "discarded when the Streamlit Cloud container restarts"
            if temporary
            else "kept while the local working directory survives"
        ),
    }


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
    url = (values.get("DATABASE_URL") or "").strip() or resolve_demo_database_url(
        values
    )
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
