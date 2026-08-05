"""Administrator-controlled pricing-data version management.

The repository in :mod:`app.ingestion.repository` is a low-level persistence
object, exactly like the SQLAlchemy repositories used elsewhere. Permission
enforcement and audit belong in a service, and this module is the only
supported way for the application to publish, activate or deactivate a
pricing-data version.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import Permission
from app.ingestion.repository import (
    PricingDataRepository,
    PricingDataVersionSummary,
)
from app.services.unit_of_work import UnitOfWork

__all__ = ["PricingDataAdminService"]


class PricingDataAdminService:
    """Publish and activate pricing data under an administrator check."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        repository: PricingDataRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repository = repository or PricingDataRepository(session_factory)

    @property
    def repository(self) -> PricingDataRepository:
        """Read-only access for listing versions."""

        return self._repository

    @staticmethod
    def _require_administrator(user: AuthenticatedUser | None) -> AuthenticatedUser:
        if user is None:
            raise PermissionDeniedError(
                "Changing the pricing data version requires an authenticated "
                "administrator."
            )
        user.require(Permission.MANAGE_DATA_VERSIONS)
        return user

    def publish(
        self, version_id: int, *, user: AuthenticatedUser | None
    ) -> PricingDataVersionSummary:
        actor = self._require_administrator(user)
        summary = self._repository.publish(version_id)
        self._audit(actor, summary, "pricing_data_version_published")
        return summary

    def activate(
        self, version_id: int, *, user: AuthenticatedUser | None
    ) -> PricingDataVersionSummary:
        actor = self._require_administrator(user)
        summary = self._repository.activate(version_id)
        self._audit(actor, summary, "pricing_data_version_activated")
        return summary

    def deactivate_all(self, *, user: AuthenticatedUser | None) -> None:
        actor = self._require_administrator(user)
        self._repository.deactivate_all()
        self._audit(actor, None, "pricing_data_versions_deactivated")

    def _audit(
        self,
        user: AuthenticatedUser,
        summary: PricingDataVersionSummary | None,
        event_type: str,
    ) -> None:
        with UnitOfWork(self._session_factory) as uow:
            uow.audit_events.append(
                quotation_id="",
                event_type=event_type,
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                after_state="active" if summary and summary.is_active else "",
                details=
                {}
                if summary is None
                else {
                    "pricing_data_version_id": summary.id,
                    "label": summary.label,
                    "status": summary.status,
                    "row_count": summary.row_count,
                    "checksum": summary.checksum,
                },
            )
            uow.commit()
