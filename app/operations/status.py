"""Operational status for the internal MVP.

Everything here is deliberately secret-free. Connection strings, passwords,
API keys and tokens are never returned: only whether a credential *is*
configured, and which environment variable would hold it.

The report is safe to expose on an internal status page or a health endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.agents.health import agent_health_report
from app.commercial_policy import active_commercial_policy, policy_key
from app.db import models
from app.db.session import get_session_factory, load_database_settings
from app.documents.assets import asset_root, load_font_configuration
from app.documents.plan import DOCUMENT_PLAN_VERSION
from app.documents.renderer import TEMPLATE_VERSION, available_pdf_engines
from app.emailing.config import load_email_config

LOGGER = logging.getLogger(__name__)

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


def redact_database_url(url: str) -> str:
    """Return a driver/host/database summary without any credential."""

    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - defensive
        return "unavailable"
    if not parts.scheme:
        return "unavailable"
    if parts.scheme.startswith("sqlite"):
        return "sqlite (local file or memory)"
    host = parts.hostname or "unknown-host"
    database = (parts.path or "").lstrip("/") or "unknown-database"
    return f"{parts.scheme}://{host}/{database}"


def application_status() -> dict[str, Any]:
    return {
        "component": "application",
        "status": OK,
        "name": "Quotation AI internal MVP",
        "phase": "phase-8",
    }


def database_status(session_factory=None) -> dict[str, Any]:
    settings = load_database_settings()
    entry: dict[str, Any] = {
        "component": "database",
        "status": UNAVAILABLE,
        "target": redact_database_url(settings.url),
        "dialect": "sqlite" if settings.is_sqlite else "external",
    }
    try:
        factory = session_factory or get_session_factory()
        with factory() as session:
            session.execute(select(1)).scalar_one()
            entry["quotation_count"] = int(
                session.execute(
                    select(func.count()).select_from(models.Quotation)
                ).scalar_one()
            )
        entry["status"] = OK
    except Exception as error:  # noqa: BLE001 - status must never raise
        entry["detail"] = type(error).__name__
    return entry


def migration_status(session_factory=None) -> dict[str, Any]:
    """Compare the database revision with the latest scripted revision."""

    entry: dict[str, Any] = {"component": "migrations", "status": UNAVAILABLE}
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        factory = session_factory or get_session_factory()
        with factory() as session:
            context = MigrationContext.configure(session.connection())
            current = context.get_current_revision()
        entry.update(
            {
                "head_revision": head,
                "current_revision": current,
                "status": OK if current == head else DEGRADED,
            }
        )
        if current != head:
            entry["detail"] = "The database is not at the latest migration."
    except Exception as error:  # noqa: BLE001 - status must never raise
        entry["detail"] = type(error).__name__
    return entry


def pricing_data_status(session_factory=None) -> dict[str, Any]:
    entry: dict[str, Any] = {"component": "pricing_data", "status": UNAVAILABLE}
    try:
        factory = session_factory or get_session_factory()
        with factory() as session:
            record = session.scalars(
                select(models.PricingDataVersion)
                .where(models.PricingDataVersion.is_active.is_(True))
                .order_by(models.PricingDataVersion.id.desc())
            ).first()
        if record is None:
            entry.update(
                {
                    "status": DEGRADED,
                    "detail": "No pricing data version is active.",
                }
            )
        else:
            entry.update(
                {
                    "status": OK,
                    "active_version_label": record.label,
                    "active_version_id": record.id,
                    "row_count": record.row_count,
                    "activated_at": _iso(record.activated_at),
                }
            )
    except Exception as error:  # noqa: BLE001
        entry["detail"] = type(error).__name__
    return entry


def commercial_policy_status() -> dict[str, Any]:
    policy = active_commercial_policy()
    return {
        "component": "commercial_policy",
        "status": OK,
        "policy_version_id": policy_key(policy),
        "policy_name": policy.policy_name,
        "policy_status": policy.status.value,
        "pass_margin_threshold_percent": str(policy.pass_margin_threshold_percent),
        "comparison": policy.comparison_operator.value,
        "missing_cost_policy": policy.missing_cost_policy.value,
        "provisional": True,
        "note": (
            "The pass-margin threshold is a provisional, active internal-MVP "
            "assumption. It is not an approved permanent company rule and is "
            "never shown in customer-facing output."
        ),
    }


def email_provider_status(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"component": "email_provider", "status": UNAVAILABLE}
    try:
        config = load_email_config(environment)
        described = config.describe()
        configured = (
            config.provider == "console"
            or config.smtp.configured
            or config.graph.configured
        )
        entry.update(
            {
                "status": OK if configured else DEGRADED,
                "provider": config.provider,
                "sender_address": config.sender_address,
                "allow_customer_delivery": config.allow_customer_delivery,
                "reminder_delay_hours": config.reminder_delay_hours,
                "smtp_configured": config.smtp.configured,
                "graph_configured": config.graph.configured,
                "body_storage": described["body_storage"],
            }
        )
        if not configured:
            entry["detail"] = "No delivery credentials are configured."
    except Exception as error:  # noqa: BLE001
        entry["detail"] = type(error).__name__
    return entry


def agents_status(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    report = agent_health_report(environment)
    healthy = all(entry.get("healthy") for entry in report.values())
    return {
        "component": "agents",
        "status": OK if healthy else DEGRADED,
        "agents": report,
        "fallback_mode": "deterministic",
    }


def reminder_worker_status(session_factory=None) -> dict[str, Any]:
    entry: dict[str, Any] = {"component": "reminder_worker", "status": UNAVAILABLE}
    try:
        factory = session_factory or get_session_factory()
        with factory() as session:
            last_sent = session.execute(
                select(func.max(models.ApprovalTask.reminder_last_sent_at))
            ).scalar_one_or_none()
            pending = int(
                session.execute(
                    select(func.count())
                    .select_from(models.ApprovalTask)
                    .where(models.ApprovalTask.status == "pending")
                ).scalar_one()
            )
        entry.update(
            {
                "status": OK,
                "last_reminder_sent_at": _iso(last_sent),
                "pending_task_count": pending,
                "entry_point": "python -m worker.reminder_worker --run-once",
            }
        )
        if last_sent is None:
            entry["detail"] = "No reminder has been dispatched yet."
    except Exception as error:  # noqa: BLE001
        entry["detail"] = type(error).__name__
    return entry


def storage_status(
    session_factory=None, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {"component": "storage", "status": UNAVAILABLE}
    try:
        factory = session_factory or get_session_factory()
        with factory() as session:
            documents = int(
                session.execute(
                    select(func.count()).select_from(models.GeneratedDocument)
                ).scalar_one()
            )
            bytes_stored = int(
                session.execute(
                    select(
                        func.coalesce(
                            func.sum(models.GeneratedDocument.byte_size), 0
                        )
                    )
                ).scalar_one()
            )
        root = asset_root(environment)
        entry.update(
            {
                "status": OK,
                "document_store": "database (generated_documents)",
                "document_count": documents,
                "stored_bytes": bytes_stored,
                "asset_repository": str(root),
                "asset_repository_present": root.is_dir(),
            }
        )
    except Exception as error:  # noqa: BLE001
        entry["detail"] = type(error).__name__
    return entry


def document_pipeline_status(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    font = load_font_configuration(environment)
    engines = available_pdf_engines()
    return {
        "component": "document_pipeline",
        "status": OK if engines else UNAVAILABLE,
        "template_version": TEMPLATE_VERSION,
        "document_plan_version": DOCUMENT_PLAN_VERSION,
        "available_engines": list(engines),
        "preferred_engine": engines[0] if engines else "",
        "font": font.describe(),
    }


def status_report(
    *,
    session_factory=None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The complete operational status report. Never raises, never leaks."""

    components = [
        application_status(),
        database_status(session_factory),
        migration_status(session_factory),
        pricing_data_status(session_factory),
        commercial_policy_status(),
        email_provider_status(environment),
        agents_status(environment),
        reminder_worker_status(session_factory),
        storage_status(session_factory, environment),
        document_pipeline_status(environment),
    ]
    overall = OK
    if any(entry["status"] == UNAVAILABLE for entry in components):
        overall = UNAVAILABLE
    elif any(entry["status"] == DEGRADED for entry in components):
        overall = DEGRADED
    return {
        "status": overall,
        "generated_at": datetime.now().astimezone().isoformat(),
        "components": {entry["component"]: entry for entry in components},
    }


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()
