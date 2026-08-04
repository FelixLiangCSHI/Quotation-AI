"""Bridge between the UI session and persistent quotation state.

This is the only module the Streamlit layer uses to obtain a workflow state.
Session state holds a quotation identifier and a version number; the trusted
state itself is loaded from the database through the service layer on every
interaction.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import replace
from typing import Any

from sqlalchemy import inspect

from app.db.base import Base
from app.db.session import get_engine
from app.domain.dto import QuotationDTO
from app.quotation_models import QuotationWorkflowState
from app.repositories.interfaces import QuotationNotFoundError
from app.services.quotation_service import LoadedQuotation, QuotationService
from app.services.session_reference import (
    clear_active_quotation,
    read_session_reference,
    set_active_quotation,
)

__all__ = [
    "active_quotation_record",
    "duplicate_active_quotation",
    "ensure_schema",
    "get_active_quotation",
    "open_quotation",
    "persist_workflow_state",
    "save_active_quotation",
    "start_new_quotation",
]


def ensure_schema() -> None:
    """Create the schema if it is missing.

    Convenience for local development and the synthetic demo so the app is
    usable immediately. Managed environments should run Alembic instead; this
    call is a no-op once the tables exist.
    """

    engine = get_engine()
    if not inspect(engine).has_table("quotations"):
        Base.metadata.create_all(engine)


def get_active_quotation(
    session_state: MutableMapping[str, Any],
    service: QuotationService | None = None,
) -> LoadedQuotation:
    """Return the session's active quotation, creating one when absent.

    The workflow state is always read from the database, never from session
    state, so two browser tabs or a restarted process observe the same
    authoritative record.
    """

    resolver = service or QuotationService()
    reference = read_session_reference(session_state)

    if reference.quotation_id:
        try:
            loaded = resolver.load_quotation(reference.quotation_id)
        except QuotationNotFoundError:
            clear_active_quotation(session_state)
        else:
            set_active_quotation(
                session_state,
                quotation_id=loaded.quotation_id,
                version=loaded.version,
            )
            return loaded

    loaded = resolver.create_quotation(actor="user")
    set_active_quotation(
        session_state,
        quotation_id=loaded.quotation_id,
        version=loaded.version,
    )
    return loaded


def save_active_quotation(
    session_state: MutableMapping[str, Any],
    loaded: LoadedQuotation,
    service: QuotationService | None = None,
    *,
    event_type: str = "quotation_updated",
    actor: str = "user",
    changed_fields: tuple[str, ...] = (),
) -> LoadedQuotation:
    """Persist the working state and refresh the session's version reference."""

    resolver = service or QuotationService()
    saved = resolver.save_state(
        loaded,
        event_type=event_type,
        actor=actor,
        changed_fields=changed_fields,
    )
    set_active_quotation(
        session_state,
        quotation_id=saved.quotation_id,
        version=saved.version,
    )
    return saved


def persist_workflow_state(
    session_state: MutableMapping[str, Any],
    state: QuotationWorkflowState,
    service: QuotationService | None = None,
    *,
    event_type: str = "quotation_updated",
    actor: str = "user",
    changed_fields: tuple[str, ...] = (),
) -> LoadedQuotation:
    """Persist a mutated working state using the session's version reference.

    Raises :class:`QuotationVersionConflictError` if another user has written
    to the quotation since this session last loaded it, so a concurrent edit
    is reported rather than silently overwritten.
    """

    resolver = service or QuotationService()
    reference = read_session_reference(session_state)
    if not reference.quotation_id or reference.quotation_version is None:
        raise QuotationNotFoundError("No active quotation in this session.")

    record = resolver.load_quotation(reference.quotation_id).record
    # Assert the version this session last rendered, not the freshly read one,
    # so a concurrent write is detected instead of being overwritten.
    loaded = LoadedQuotation(
        record=replace(record, version=reference.quotation_version),
        state=state,
    )
    return save_active_quotation(
        session_state,
        loaded,
        resolver,
        event_type=event_type,
        actor=actor,
        changed_fields=changed_fields,
    )


def start_new_quotation(
    session_state: MutableMapping[str, Any],
    service: QuotationService | None = None,
    *,
    state: QuotationWorkflowState | None = None,
) -> LoadedQuotation:
    """Create a fresh quotation and make it the session's active one."""

    resolver = service or QuotationService()
    loaded = resolver.create_quotation(actor="user", state=state)
    set_active_quotation(
        session_state,
        quotation_id=loaded.quotation_id,
        version=loaded.version,
    )
    return loaded


def duplicate_active_quotation(
    session_state: MutableMapping[str, Any],
    quotation_id: str,
    service: QuotationService | None = None,
    *,
    as_new_version: bool = False,
) -> LoadedQuotation:
    """Copy a quotation and make the copy this session's active quotation."""

    resolver = service or QuotationService()
    copy = (
        resolver.clone_as_new_version(quotation_id, actor="user")
        if as_new_version
        else resolver.duplicate_quotation(quotation_id, actor="user")
    )
    set_active_quotation(
        session_state,
        quotation_id=copy.quotation_id,
        version=copy.version,
    )
    return copy


def open_quotation(
    session_state: MutableMapping[str, Any],
    quotation_id: str,
    service: QuotationService | None = None,
) -> LoadedQuotation:
    """Reopen a previously persisted quotation in this session."""

    resolver = service or QuotationService()
    loaded = resolver.load_quotation(quotation_id)
    set_active_quotation(
        session_state,
        quotation_id=loaded.quotation_id,
        version=loaded.version,
    )
    return loaded


def active_quotation_record(
    session_state: MutableMapping[str, Any],
    service: QuotationService | None = None,
) -> QuotationDTO:
    """Return the typed DTO for the active quotation.

    The UI renders from this DTO; SQLAlchemy models are never exposed.
    """

    return get_active_quotation(session_state, service).record
