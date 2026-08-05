"""Role-restricted internal audit view (Phase 6).

Audit records are only readable by a principal holding
:data:`Permission.VIEW_AUDIT_RECORDS`. Records never contain a password hash,
a session token or any other credential; this module additionally strips any
key whose name looks secret before returning a record.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import Permission
from app.domain.dto import AuditEventDTO
from app.services.unit_of_work import UnitOfWork

#: Detail keys that must never be returned by the audit view.
SECRET_DETAIL_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "token",
        "session_token",
        "api_key",
        "credential",
        "credentials",
    }
)


def redact_details(details: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (details or {}).items()
        if key.casefold() not in SECRET_DETAIL_KEYS
    }


class AuditViewService:
    """Read access to the persistent audit trail."""

    def __init__(
        self, session_factory: sessionmaker[Session] | None = None
    ) -> None:
        self._session_factory = session_factory

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    @staticmethod
    def _require(user: AuthenticatedUser | None) -> None:
        if user is None:
            raise PermissionDeniedError(
                "An authenticated user is required to view audit records."
            )
        user.require(Permission.VIEW_AUDIT_RECORDS)

    def list_for_quotation(
        self, user: AuthenticatedUser, quotation_id: str
    ) -> tuple[AuditEventDTO, ...]:
        self._require(user)
        with self._unit_of_work() as uow:
            events = uow.audit_events.list_for_quotation(quotation_id)
        return tuple(self._sanitised(event) for event in events)

    def list_recent(
        self, user: AuthenticatedUser, *, limit: int = 200
    ) -> tuple[AuditEventDTO, ...]:
        self._require(user)
        with self._unit_of_work() as uow:
            events = uow.audit_events.list_recent(limit=limit)
        return tuple(self._sanitised(event) for event in events)

    @staticmethod
    def _sanitised(event: AuditEventDTO) -> AuditEventDTO:
        from dataclasses import replace

        return replace(event, details=redact_details(event.details))
