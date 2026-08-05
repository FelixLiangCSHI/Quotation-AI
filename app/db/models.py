"""SQLAlchemy 2.x ORM models for the internal MVP.

These models are infrastructure detail. They are never handed to the UI
directly; services convert them into the typed DTOs in :mod:`app.domain`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import JSONDocument, UTCDateTime

# Money is stored as an exact decimal to avoid binary floating point drift on
# margin and minimum-price comparisons.
MONEY = Numeric(18, 4)
PERCENT = Numeric(9, 4)


class User(Base, TimestampMixin):
    """An internal user account with locally managed credentials.

    ``password_hash`` stores only a PBKDF2 digest; no password or secret is
    ever persisted in clear text. ``auth_provider`` records which
    authentication source owns the account so a future enterprise SSO provider
    can coexist with local accounts.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    roles: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    auth_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="local"
    )
    external_subject: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    owned_quotations: Mapped[list["Quotation"]] = relationship(
        back_populates="owner",
        foreign_keys="Quotation.owner_user_id",
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserSession(Base, TimestampMixin):
    """A persistent authenticated session.

    The token is a high-entropy random value; it is a credential, so it is
    never written to an audit record.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class PricingDataVersion(Base, TimestampMixin):
    """A version of the offline SAP/Excel pricing dataset.

    Phase 2 fills this table from the ingestion pipeline. A version is created
    ``staged``, becomes ``published`` on an explicit publish action, and only
    one version may be ``is_active`` at a time. Activation is always an
    explicit user action; nothing switches the active version implicitly.
    """

    __tablename__ = "pricing_data_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="staged"
    )
    source_filename: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- Phase 2: offline Excel ingestion metadata ------------------------
    source_kind: Mapped[str] = mapped_column(
        String(50), nullable=False, default="excel_import"
    )
    uploaded_by: Mapped[str] = mapped_column(
        String(150), nullable=False, default=""
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    storage_uri: Mapped[str] = mapped_column(
        String(1000), nullable=False, default=""
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    warning_row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    rejected_row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    mapping_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    records: Mapped[list["PricingDataRecord"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rejections: Mapped[list["PricingDataRejection"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_pricing_data_versions_checksum", "checksum"),
        Index("ix_pricing_data_versions_is_active", "is_active"),
    )


class PricingDataRecord(Base):
    """One accepted canonical row belonging to a pricing data version."""

    __tablename__ = "pricing_data_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_data_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    source_sheet: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    source_row_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    product_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    has_warnings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    version: Mapped["PricingDataVersion"] = relationship(back_populates="records")

    __table_args__ = (
        Index(
            "ix_pricing_data_records_version_dataset",
            "version_id",
            "dataset_kind",
        ),
        Index("ix_pricing_data_records_product", "version_id", "product_id"),
    )


class PricingDataRejection(Base):
    """A quarantined row. Rejected rows never enter the active dataset."""

    __tablename__ = "pricing_data_rejections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_data_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    source_sheet: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    source_row_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    issues: Mapped[list[Any]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    version: Mapped["PricingDataVersion"] = relationship(
        back_populates="rejections"
    )

    __table_args__ = (
        Index("ix_pricing_data_rejections_version", "version_id"),
    )


class ColumnMappingProfileRecord(Base, TimestampMixin):
    """A saved, reusable Excel column mapping."""

    __tablename__ = "column_mapping_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    dataset_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    sheet_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    header_row: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )
    created_by: Mapped[str] = mapped_column(
        String(150), nullable=False, default=""
    )


class Quotation(Base, TimestampMixin):
    """The persistent root of a quotation."""

    __tablename__ = "quotations"
    __table_args__ = (
        Index("ix_quotations_status", "status"),
        Index("ix_quotations_owner_user_id", "owner_user_id"),
    )

    # Database primary key, distinct from the stable business identifier.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )
    customer_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    incoterm: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    delivery_location: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    approval_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="not_ready"
    )
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Optimistic concurrency. Managed explicitly by the repository so that a
    # stale write raises rather than silently overwriting a concurrent edit.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    pricing_data_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_data_versions.id", ondelete="SET NULL"), nullable=True
    )

    # Serialised QuotationWorkflowState. Reuses the existing dataclass graph
    # verbatim so no deterministic logic has to be rewritten.
    state_document: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )
    state_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )

    owner: Mapped[User | None] = relationship(
        back_populates="owned_quotations", foreign_keys=[owner_user_id]
    )
    line_items: Mapped[list["QuotationLineItem"]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        order_by="QuotationLineItem.position",
    )
    pricing_runs: Mapped[list["PricingRun"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )
    technical_validation_runs: Mapped[list["TechnicalValidationRun"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )
    commercial_validation_runs: Mapped[list["CommercialValidationRun"]] = (
        relationship(back_populates="quotation", cascade="all, delete-orphan")
    )
    combined_decisions: Mapped[list["CombinedDecisionRecord"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )
    approval_tasks: Mapped[list["ApprovalTask"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEventRecord"]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        order_by="AuditEventRecord.id",
    )
    documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )
    email_records: Mapped[list["EmailRecord"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )


class QuotationLineItem(Base, TimestampMixin):
    """A single priced position on a quotation.

    ``item_type`` distinguishes main products, accessories, services,
    installation, warranty and freight or other commercial additions.
    """

    __tablename__ = "quotation_line_items"
    __table_args__ = (
        UniqueConstraint(
            "quotation_id", "position", name="uq_line_item_position"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    item_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="main_product"
    )
    product_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Internal description may reference cost or sourcing detail.
    internal_description: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    # Customer-safe description is the only text allowed in customer output.
    customer_description: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    proposed_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    approved_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    list_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    discount_percent: Mapped[Any | None] = mapped_column(PERCENT, nullable=True)
    is_optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    quotation: Mapped[Quotation] = relationship(back_populates="line_items")


class PricingRun(Base, TimestampMixin):
    """An immutable record of one deterministic pricing analysis."""

    __tablename__ = "pricing_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    pricing_data_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_data_versions.id", ondelete="SET NULL"), nullable=True
    )
    engine: Mapped[str] = mapped_column(
        String(100), nullable=False, default="deterministic"
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    recommended_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    total_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    confidence_label: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    quotation: Mapped[Quotation] = relationship(back_populates="pricing_runs")


class TechnicalValidationRun(Base, TimestampMixin):
    """An immutable record of one deterministic technical validation."""

    __tablename__ = "technical_validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="not_checked"
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    quotation: Mapped[Quotation] = relationship(
        back_populates="technical_validation_runs"
    )


class CommercialValidationRun(Base, TimestampMixin):
    """An immutable record of one deterministic commercial validation."""

    __tablename__ = "commercial_validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="not_checked"
    )
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )

    quotation: Mapped[Quotation] = relationship(
        back_populates="commercial_validation_runs"
    )


class CombinedDecisionRecord(Base, TimestampMixin):
    """The deterministic logical judgement derived from both validations."""

    __tablename__ = "combined_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    technical_validation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("technical_validation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    commercial_validation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("commercial_validation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    recommended_next_action: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    triggered_rule_ids: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )

    quotation: Mapped[Quotation] = relationship(back_populates="combined_decisions")


class ApprovalTask(Base, TimestampMixin):
    """A pending or completed approval assigned to a named internal approver."""

    __tablename__ = "approval_tasks"
    __table_args__ = (Index("ix_approval_tasks_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", index=True
    )
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    decision_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    assigned_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_approver_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    assigned_approver_role: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending_review"
    )
    decision: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reminder_due_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    # Reminder bookkeeping. Persisted so a web-process restart cannot lose
    # reminder state and a second worker run cannot resend the same cycle.
    reminder_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reminder_sent_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    reminder_last_sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    reminder_claimed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    reminder_last_error_category: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    reminder_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    policy_version_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    pricing_run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    validation_run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )

    quotation: Mapped[Quotation] = relationship(back_populates="approval_tasks")
    actions: Mapped[list["ApprovalAction"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ApprovalAction.id",
    )
    overrides: Mapped[list["ApprovalOverrideRecord"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ApprovalOverrideRecord.id",
    )


class ApprovalAction(Base, TimestampMixin):
    """One recorded approval action.

    ``action_id`` is globally unique so a replayed submission is rejected by
    the database, not only by in-memory comparison against the last action.
    """

    __tablename__ = "approval_actions"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_approval_action_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_task_id: Mapped[int] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    actor_role: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    from_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    original_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    final_unit_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    triggered_rule_ids: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    task: Mapped[ApprovalTask] = relationship(back_populates="actions")


class ApprovalOverrideRecord(Base, TimestampMixin):
    """The documented justification for an approval below policy threshold."""

    __tablename__ = "approval_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_task_id: Mapped[int] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approval_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_actions.id", ondelete="SET NULL"), nullable=True
    )
    original_decision: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    evaluated_margin_percent: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    policy_threshold_percent: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    policy_version_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    approver_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approver_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    approver_role: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False, default="")
    final_approved_price: Mapped[Any | None] = mapped_column(MONEY, nullable=True)
    final_margin_percent: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    triggered_rule_ids: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    task: Mapped[ApprovalTask] = relationship(back_populates="overrides")


class AuditEventRecord(Base):
    """An append-only record of a material workflow event."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_event_type", "event_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quotation_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    quotation_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False, default="system")
    actor_role: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    policy_version_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    before_state: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    after_state: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    changed_fields: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    triggered_rule_ids: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    quotation: Mapped[Quotation | None] = relationship(back_populates="audit_events")


class GeneratedDocument(Base, TimestampMixin):
    """A generated, downloadable and auditable artefact.

    The Phase 8 metadata columns make every customer document traceable to the
    exact quotation version, approval action, template, document plan and
    agent provider that produced it. Historical documents are retained and
    stay associated with their original quotation version; a material edit
    marks them superseded rather than deleting them.
    """

    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", index=True
    )
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    approval_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_actions.id", ondelete="SET NULL"), nullable=True
    )
    # customer_pdf | internal_audit_export | customer_export
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    audience: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal"
    )
    template_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    document_plan_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    agent_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    render_engine: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    generated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    storage_reference: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: generated | superseded | failed
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="generated", index=True
    )
    error_category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="none"
    )
    generated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    quotation: Mapped[Quotation] = relationship(back_populates="documents")


class EmailRecord(Base, TimestampMixin):
    """A composed email, its delivery outcome and its provenance.

    The full body is persisted only when ``EMAIL_BODY_STORAGE=full``. The
    default keeps a body hash plus template metadata so an internal MVP does
    not accumulate sensitive message content it does not need.
    """

    __tablename__ = "email_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_email_idempotency_key"),
        Index("ix_email_records_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    quotation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    approval_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # approval_request | approval_reminder | customer_quotation |
    # revision_request | rejection_notification
    email_type: Mapped[str] = mapped_column(String(50), nullable=False)
    audience: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal"
    )
    sender: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    recipients: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    cc_recipients: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    bcc_recipients: Mapped[list[str]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_storage_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="hash"
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    template_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1"
    )
    agent_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="deterministic"
    )
    agent_fallback_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    agent_fallback_reason: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    delivery_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="console"
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="drafted")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )
    last_error_detail: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    provider_message_id: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    attachment_document_ids: Mapped[list[int]] = mapped_column(
        JSONDocument, nullable=False, default=list
    )
    reminder_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    quotation: Mapped[Quotation] = relationship(back_populates="email_records")
