"""Customer document generation, access control and retention (Phase 8).

Generation gate
---------------
A customer quotation PDF is generated **only** when the persisted approval
status is ``approved`` or ``approved_with_override``. Draft, PASS-but-not-yet-
approved, REVIEW_REQUIRED pending approval, BLOCKED, revision-requested,
rejected and stale-version quotations are refused.

PASS is a deterministic *decision*, never an approval. The approval status
comes from the persisted approval record written by
:mod:`app.services.approval_service`.

Retention
---------
A material quotation edit supersedes previously generated customer documents
for current-use purposes. Superseded documents are retained and remain
associated with their original quotation version so the audit trail stays
complete.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.agents.agents import Agent4DocumentPlanAgent, DocumentPlanRequest
from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import Permission
from app.documents.context import (
    APPROVED_STATUSES,
    CustomerDocumentContext,
    DocumentContextError,
    build_customer_document_context,
)
from app.documents.plan import (
    ALLOWED_CHART_IDS,
    DEFAULT_SECTION_HEADINGS,
    DEFAULT_SECTION_IDS,
    DocumentPlan,
    build_document_plan,
    deterministic_document_plan,
)
from app.documents.renderer import (
    DocumentRenderError,
    RenderedDocument,
    render_quotation_pdf,
)
from app.services.quotation_service import QuotationService
from app.services.unit_of_work import UnitOfWork

LOGGER = logging.getLogger(__name__)

CUSTOMER_PDF_KIND = "customer_pdf"
INTERNAL_AUDIT_KIND = "internal_audit_export"

#: Permissions that allow reading a customer document.
CUSTOMER_DOWNLOAD_PERMISSIONS = (
    Permission.VIEW_OWN_QUOTATIONS,
    Permission.VIEW_APPROVAL_TASKS,
    Permission.VIEW_AUDIT_RECORDS,
)


class DocumentServiceError(RuntimeError):
    """Base class for document workflow failures."""


class DocumentNotAllowedError(DocumentServiceError):
    """Raised when a document must not be generated or released."""


class DocumentNotFoundError(DocumentServiceError):
    """Raised when a requested document does not exist."""


@dataclass(frozen=True)
class DocumentMetadata:
    """Persisted, secret-free provenance for one generated document."""

    id: int
    document_id: str
    quotation_id: str
    quotation_version: int
    approval_action_id: int | None
    document_type: str
    audience: str
    template_version: str
    document_plan_version: str
    agent_provider: str
    render_engine: str
    generated_at: Any
    file_hash: str
    mime_type: str
    filename: str
    storage_reference: str
    byte_size: int
    status: str
    error_category: str


@dataclass(frozen=True)
class GeneratedCustomerDocument:
    """A generated document plus its metadata and bytes."""

    metadata: DocumentMetadata
    content: bytes

    @property
    def filename(self) -> str:
        return self.metadata.filename

    @property
    def mime_type(self) -> str:
        return self.metadata.mime_type


def _metadata(record) -> DocumentMetadata:
    return DocumentMetadata(
        id=record.id,
        document_id=record.document_id,
        quotation_id=record.quotation_reference,
        quotation_version=record.quotation_version,
        approval_action_id=record.approval_action_id,
        document_type=record.kind,
        audience=record.audience,
        template_version=record.template_version,
        document_plan_version=record.document_plan_version,
        agent_provider=record.agent_provider,
        render_engine=record.render_engine,
        generated_at=record.generated_at,
        file_hash=record.checksum,
        mime_type=record.mime_type,
        filename=record.filename,
        storage_reference=record.storage_reference,
        byte_size=record.byte_size,
        status=record.status,
        error_category=record.error_category,
    )


class DocumentService:
    """Generate, store, release and invalidate customer documents."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        quotation_service: QuotationService | None = None,
        plan_agent: Agent4DocumentPlanAgent | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._quotations = quotation_service or QuotationService(session_factory)
        self._plan_agent = plan_agent
        self._environment = environment

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    # -- authorisation -------------------------------------------------

    @staticmethod
    def _require_authenticated(user: AuthenticatedUser | None) -> AuthenticatedUser:
        if user is None:
            raise PermissionDeniedError(
                "An authenticated user is required for document actions."
            )
        return user

    @staticmethod
    def _require_any(
        user: AuthenticatedUser, permissions=CUSTOMER_DOWNLOAD_PERMISSIONS
    ) -> None:
        if not any(user.has_permission(permission) for permission in permissions):
            raise PermissionDeniedError(
                "This account may not access quotation documents."
            )

    def _require_object_access(
        self, user: AuthenticatedUser, record
    ) -> None:
        """Object-level check: owning, approving or auditing this quotation."""

        if user.has_permission(Permission.VIEW_AUDIT_RECORDS):
            return
        if user.has_permission(Permission.VIEW_APPROVAL_TASKS):
            return
        if record.owner_user_id is not None and record.owner_user_id == user.user_id:
            return
        raise PermissionDeniedError(
            "This account may not access documents for this quotation."
        )

    # -- generation ----------------------------------------------------

    def generate_customer_pdf(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser | None,
        quotation_version: int | None = None,
        as_of: date | None = None,
        include_charts: bool = True,
        logo_asset: str | None = None,
        regenerate: bool = False,
    ) -> GeneratedCustomerDocument:
        """Generate (or return) the approved customer PDF for a version."""

        actor = self._require_authenticated(user)
        self._require_any(actor)
        loaded = self._quotations.load_quotation(quotation_id)
        record = loaded.record
        self._require_object_access(actor, record)

        if quotation_version is not None and quotation_version != record.version:
            raise DocumentNotAllowedError(
                "A customer document can only be generated for the current "
                "quotation version."
            )
        version = record.version
        self._assert_generation_allowed(record, loaded.state)

        with self._unit_of_work() as uow:
            existing = uow.documents.latest_for_version(
                quotation_id=quotation_id,
                quotation_version=version,
                kind=CUSTOMER_PDF_KIND,
            )
            if existing is not None and not regenerate:
                return GeneratedCustomerDocument(
                    metadata=_metadata(existing), content=existing.content
                )

        try:
            context = build_customer_document_context(
                loaded.state, quotation_version=version, as_of=as_of
            )
        except DocumentContextError as error:
            self._record_failure(
                quotation_id,
                version=version,
                user=actor,
                error_category="context_unavailable",
                detail=str(error),
            )
            raise DocumentNotAllowedError(str(error)) from error

        plan = self.build_plan(context)
        try:
            rendered = render_quotation_pdf(
                context,
                plan,
                include_charts=include_charts,
                logo_asset=logo_asset,
                environment=self._environment,
            )
        except DocumentRenderError as error:
            self._record_failure(
                quotation_id,
                version=version,
                user=actor,
                error_category="render_failed",
                detail=type(error).__name__,
            )
            raise DocumentServiceError(
                "The customer quotation document could not be rendered."
            ) from error

        return self._persist(
            quotation_id,
            version=version,
            user=actor,
            rendered=rendered,
            plan=plan,
        )

    def build_plan(self, context: CustomerDocumentContext) -> DocumentPlan:
        """Ask Agent 4 for a plan and validate it, or use the baseline."""

        agent = self._plan_agent
        if agent is None:
            try:
                agent = Agent4DocumentPlanAgent()
            except Exception as error:  # noqa: BLE001 - never block a document
                LOGGER.info(
                    "Agent 4 is unavailable (%s); using the deterministic plan.",
                    type(error).__name__,
                )
                return deterministic_document_plan()
        request = DocumentPlanRequest(
            section_ids=DEFAULT_SECTION_IDS,
            section_headings=dict(DEFAULT_SECTION_HEADINGS),
            allowed_chart_ids=ALLOWED_CHART_IDS,
        )
        try:
            outcome = agent.run(request)
        except Exception as error:  # noqa: BLE001 - never block a document
            LOGGER.info(
                "Agent 4 invocation failed (%s); using the deterministic plan.",
                type(error).__name__,
            )
            return deterministic_document_plan(
                provider=getattr(agent.config, "provider", "deterministic"),
                fallback_reason="agent invocation failed",
            )
        return build_document_plan(
            outcome.value,
            allowed_section_ids=DEFAULT_SECTION_IDS,
            provider=outcome.audit.provider,
            ai_generated=not outcome.fallback_used,
            fallback_reason=outcome.audit.error_category.value
            if outcome.fallback_used
            else "",
        )

    # -- gating --------------------------------------------------------

    @staticmethod
    def _assert_generation_allowed(record, state) -> None:
        approval_status = state.approval.status
        if approval_status not in APPROVED_STATUSES:
            raise DocumentNotAllowedError(
                "A customer quotation PDF requires an approved quotation. "
                "A PASS decision is not an approval."
            )
        if record.approval_status not in {status.value for status in APPROVED_STATUSES}:
            raise DocumentNotAllowedError(
                "The persisted approval status does not permit a customer "
                "quotation PDF."
            )
        if record.is_closed:
            raise DocumentNotAllowedError(
                "A closed quotation cannot produce a new customer document."
            )
        if state.validation_stale or state.combined_decision is None:
            raise DocumentNotAllowedError(
                "Pricing and validation must be current before a customer "
                "document is generated."
            )
        if state.combined_decision.status == "blocked":
            raise DocumentNotAllowedError(
                "A blocked quotation cannot produce a customer document."
            )

    # -- persistence and audit -----------------------------------------

    def _persist(
        self,
        quotation_id: str,
        *,
        version: int,
        user: AuthenticatedUser,
        rendered: RenderedDocument,
        plan: DocumentPlan,
    ) -> GeneratedCustomerDocument:
        with self._unit_of_work() as uow:
            approval_action_id = self._latest_approval_action_id(uow, quotation_id)
            document_pk = uow.documents.add(
                quotation_id=quotation_id,
                kind=CUSTOMER_PDF_KIND,
                audience="customer",
                filename=rendered.filename,
                mime_type=rendered.mime_type,
                content=rendered.content,
                quotation_version=version,
                generated_by_user_id=user.user_id,
                approval_action_id=approval_action_id,
                template_version=rendered.template_version,
                document_plan_version=rendered.plan_version,
                agent_provider=plan.provider,
                render_engine=rendered.engine,
                status="generated",
                error_category="none",
            )
            record = uow.documents.get(document_pk)
            assert record is not None
            uow.audit_events.append(
                quotation_id=quotation_id,
                event_type="customer_document_generated",
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                after_state="generated",
                quotation_version=version,
                details={
                    "document_id": record.document_id,
                    "document_type": CUSTOMER_PDF_KIND,
                    "template_version": record.template_version,
                    "document_plan_version": record.document_plan_version,
                    "agent_provider": record.agent_provider,
                    "render_engine": record.render_engine,
                    "file_hash": record.checksum,
                    "mime_type": record.mime_type,
                    "byte_size": record.byte_size,
                },
            )
            metadata = _metadata(record)
            content = record.content
            uow.commit()
        return GeneratedCustomerDocument(metadata=metadata, content=content)

    def _record_failure(
        self,
        quotation_id: str,
        *,
        version: int,
        user: AuthenticatedUser,
        error_category: str,
        detail: str,
    ) -> None:
        with self._unit_of_work() as uow:
            uow.audit_events.append(
                quotation_id=quotation_id,
                event_type="customer_document_generation_failed",
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                after_state="failed",
                reason=detail[:500],
                quotation_version=version,
                details={"error_category": error_category},
            )
            uow.commit()

    @staticmethod
    def _latest_approval_action_id(uow: UnitOfWork, quotation_id: str) -> int | None:
        tasks = uow.approvals.list_tasks(quotation_id=quotation_id)
        for task in reversed(tasks):
            actions = uow.approvals.list_actions(task_id=task.id)
            if actions:
                return actions[-1].id
        return None

    # -- reads and downloads -------------------------------------------

    def list_documents(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser | None,
        kind: str | None = None,
    ) -> tuple[DocumentMetadata, ...]:
        actor = self._require_authenticated(user)
        self._require_any(actor)
        loaded = self._quotations.load_quotation(quotation_id)
        self._require_object_access(actor, loaded.record)
        with self._unit_of_work() as uow:
            records = uow.documents.list_for_quotation(
                quotation_id=quotation_id, kind=kind
            )
            return tuple(_metadata(record) for record in records)

    def download_customer_document(
        self,
        document_id: str,
        *,
        user: AuthenticatedUser | None,
        allow_superseded: bool = False,
    ) -> GeneratedCustomerDocument:
        """Customer-safe download of a persisted customer document."""

        actor = self._require_authenticated(user)
        self._require_any(actor)
        with self._unit_of_work() as uow:
            record = uow.documents.get_by_document_id(document_id)
            if record is None or record.kind != CUSTOMER_PDF_KIND:
                raise DocumentNotFoundError("Unknown customer document.")
            quotation_reference = record.quotation_reference
            metadata = _metadata(record)
            content = record.content
        loaded = self._quotations.load_quotation(quotation_reference)
        self._require_object_access(actor, loaded.record)
        if metadata.status != "generated" and not allow_superseded:
            raise DocumentNotAllowedError(
                "This document was superseded by a later quotation version and "
                "must not be sent to a customer."
            )
        self._record_download(quotation_reference, actor, metadata, "customer")
        return GeneratedCustomerDocument(metadata=metadata, content=content)

    def export_internal_audit_document(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser | None,
    ) -> GeneratedCustomerDocument:
        """Restricted internal audit export of the document register."""

        actor = self._require_authenticated(user)
        actor.require(Permission.VIEW_AUDIT_RECORDS)
        from app.audit_export import export_json_bytes

        loaded = self._quotations.load_quotation(quotation_id)
        with self._unit_of_work() as uow:
            records = uow.documents.list_for_quotation(quotation_id=quotation_id)
            payload = {
                "quotation_id": quotation_id,
                "current_quotation_version": loaded.record.version,
                "documents": [
                    {
                        "document_id": record.document_id,
                        "quotation_version": record.quotation_version,
                        "approval_action_id": record.approval_action_id,
                        "document_type": record.kind,
                        "audience": record.audience,
                        "template_version": record.template_version,
                        "document_plan_version": record.document_plan_version,
                        "agent_provider": record.agent_provider,
                        "render_engine": record.render_engine,
                        "generated_at": (
                            None
                            if record.generated_at is None
                            else record.generated_at.isoformat()
                        ),
                        "file_hash": record.checksum,
                        "mime_type": record.mime_type,
                        "storage_reference": record.storage_reference,
                        "byte_size": record.byte_size,
                        "status": record.status,
                        "error_category": record.error_category,
                    }
                    for record in records
                ],
            }
            content = export_json_bytes(payload)
            uow.audit_events.append(
                quotation_id=quotation_id,
                event_type="document_register_exported",
                actor=actor.username,
                actor_role=actor.primary_role.value,
                actor_user_id=actor.user_id,
                quotation_version=loaded.record.version,
                details={"document_count": len(records)},
            )
            uow.commit()
        from app.documents.renderer import safe_document_filename

        return GeneratedCustomerDocument(
            metadata=DocumentMetadata(
                id=0,
                document_id="",
                quotation_id=quotation_id,
                quotation_version=loaded.record.version,
                approval_action_id=None,
                document_type=INTERNAL_AUDIT_KIND,
                audience="internal",
                template_version="",
                document_plan_version="",
                agent_provider="",
                render_engine="json",
                generated_at=None,
                file_hash="",
                mime_type="application/json",
                filename=safe_document_filename(
                    f"{quotation_id}-document-register",
                    quotation_version=loaded.record.version,
                    suffix="json",
                ),
                storage_reference="",
                byte_size=len(content),
                status="generated",
                error_category="none",
            ),
            content=content,
        )

    def _record_download(
        self,
        quotation_id: str,
        user: AuthenticatedUser,
        metadata: DocumentMetadata,
        audience: str,
    ) -> None:
        with self._unit_of_work() as uow:
            uow.audit_events.append(
                quotation_id=quotation_id,
                event_type="customer_document_downloaded",
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                quotation_version=metadata.quotation_version,
                details={
                    "document_id": metadata.document_id,
                    "audience": audience,
                    "file_hash": metadata.file_hash,
                },
            )
            uow.commit()

    # -- invalidation --------------------------------------------------

    def invalidate_for_material_edit(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser | None = None,
        new_version: int,
    ) -> tuple[int, ...]:
        """Supersede customer documents produced before ``new_version``.

        Nothing is deleted: historical approved documents stay retained and
        associated with the quotation version that produced them.
        """

        actor_name = "system" if user is None else user.username
        actor_role = "" if user is None else user.primary_role.value
        with self._unit_of_work() as uow:
            superseded = uow.documents.supersede_for_quotation(
                quotation_id=quotation_id,
                before_version=new_version,
                kind=CUSTOMER_PDF_KIND,
            )
            if superseded:
                uow.audit_events.append(
                    quotation_id=quotation_id,
                    event_type="customer_documents_superseded",
                    actor=actor_name,
                    actor_role=actor_role,
                    actor_user_id=None if user is None else user.user_id,
                    after_state="superseded",
                    reason="Material quotation edit invalidated current documents.",
                    quotation_version=new_version,
                    details={"document_count": len(superseded)},
                )
            uow.commit()
        return superseded
