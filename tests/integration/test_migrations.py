"""The Alembic migration must reproduce the ORM schema exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.base import Base
from app.db.session import DatabaseSettings, create_database_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "approval_actions",
    "approval_overrides",
    "approval_tasks",
    "audit_events",
    "column_mapping_profiles",
    "combined_decisions",
    "commercial_validation_runs",
    "email_records",
    "generated_documents",
    "pricing_data_records",
    "pricing_data_rejections",
    "pricing_data_versions",
    "pricing_runs",
    "quotation_line_items",
    "quotations",
    "technical_validation_runs",
    "user_sessions",
    "users",
}


@pytest.fixture()
def migrated_database(tmp_path):
    database_path = tmp_path / "migration_check.db"
    url = f"sqlite+pysqlite:///{database_path}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_migration_creates_every_expected_table(migrated_database):
    config, url = migrated_database

    upgrade(config, "head")

    engine = create_database_engine(DatabaseSettings(url=url))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables


def test_migrated_schema_matches_the_orm_models(migrated_database):
    """``alembic check`` equivalent: no drift between models and migrations."""

    config, url = migrated_database
    upgrade(config, "head")

    engine = create_database_engine(DatabaseSettings(url=url))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True},
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == [], f"Schema drift detected: {differences}"


def test_migration_can_be_rolled_back(migrated_database):
    config, url = migrated_database
    upgrade(config, "head")

    downgrade(config, "base")

    engine = create_database_engine(DatabaseSettings(url=url))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (EXPECTED_TABLES & tables)


def test_alembic_ini_contains_no_credentials():
    content = (REPOSITORY_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url =" not in content
    for marker in ("password", "postgresql://", "@localhost"):
        assert marker not in content.casefold()
