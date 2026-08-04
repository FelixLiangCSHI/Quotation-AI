from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from app.serialization import to_customer_jsonable, to_jsonable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowStage(str, Enum):
    DRAFT = "draft"
    COLLECTING_REQUIREMENTS = "collecting_requirements"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    ANALYSED = "analysed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    DOCUMENTS_READY = "documents_ready"


class LineItemCategory(str, Enum):
    """Commercial category of a quotation line item."""

    MAIN_PRODUCT = "main_product"
    ACCESSORY = "accessory"
    INSTALLATION = "installation"
    WARRANTY = "warranty"
    SERVICE = "service"
    COMMERCIAL_ADDITION = "commercial_addition"


class RecommendationStatus(str, Enum):
    """How a recommended item relates to the current configuration."""

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    INCOMPATIBLE = "incompatible"
    NOT_EVALUATED = "not_evaluated"


@dataclass
class QuotationLineItem:
    """One priced or priceable line on a quotation.

    A single-product quotation is simply a one-element collection, so existing
    single-item behaviour is preserved.
    """

    line_id: str
    product_id: str = ""
    description: str = ""
    category: LineItemCategory = LineItemCategory.MAIN_PRODUCT
    quantity: int = 1
    unit_price: float | None = None
    is_optional: bool = False
    source: str = "manual"
    notes: str = ""

    def __post_init__(self) -> None:
        self.category = LineItemCategory(self.category)
        if self.quantity < 1:
            raise ValueError("Line item quantity must be at least 1.")

    @property
    def extended_price(self) -> float | None:
        if self.unit_price is None:
            return None
        return self.unit_price * self.quantity


class ApprovalStatus(str, Enum):
    NOT_READY = "not_ready"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    APPROVED_WITH_OVERRIDE = "approved_with_override"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


@dataclass
class QuotationDraft:
    quotation_id: str
    customer_name: str = ""
    customer_type: str = ""
    region: str = ""
    product_query: str = ""
    selected_product_ids: list[str] = field(default_factory=list)
    quantity: int = 1
    currency: str = "USD"
    incoterm: str = ""
    delivery_location: str = ""
    requested_delivery_date: date | None = None
    target_price: float | None = None
    proposed_unit_price: float | None = None
    intended_use: str = ""
    budget_notes: str = ""
    requested_accessories: list[str] = field(default_factory=list)
    requested_services: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    line_items: list[QuotationLineItem] = field(default_factory=list)
    pending_confirmations: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    missing_fields: list[str] = field(default_factory=list)
    status: WorkflowStage = WorkflowStage.DRAFT
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.status = WorkflowStage(self.status)


@dataclass
class ComparableQuotation:
    source_id: str = field(default="", metadata={"customer_visible": False})
    source_sheet: str = field(default="", metadata={"customer_visible": False})
    product_id: str = ""
    description: str = ""
    quantity: int = 0
    list_price: float | None = None
    net_price: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    minimum_price: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    cost: float | None = field(default=None, metadata={"customer_visible": False})
    currency: str = ""
    match_score: float = field(default=0.0, metadata={"customer_visible": False})
    match_reasons: list[str] = field(
        default_factory=list, metadata={"customer_visible": False}
    )


@dataclass
class PricingResult:
    selected_product_ids: list[str] = field(default_factory=list)
    currency: str = ""
    recommended_unit_price: float | None = None
    total_price: float | None = None
    reference_list_price: float | None = None
    reference_net_price: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    comparable_median_price: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    minimum_price_floor: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    gross_margin_floor: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    estimated_cost: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    gross_margin_amount: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    gross_margin_percent: float | None = field(
        default=None, metadata={"customer_visible": False}
    )
    discount_percent: float | None = None
    comparable_count: int = field(default=0, metadata={"customer_visible": False})
    cost_basis_complete: bool = field(
        default=False, metadata={"customer_visible": False}
    )
    confidence_score: float = 0.0
    confidence_label: str = ""
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    internal_evidence: list[ComparableQuotation | dict[str, Any]] = field(
        default_factory=list, metadata={"customer_visible": False}
    )

    def to_customer_dict(self) -> dict[str, Any]:
        return to_customer_jsonable(self)


@dataclass
class TechnicalValidationResult:
    status: str = "not_checked"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    not_evaluated_checks: list[str] = field(default_factory=list)
    evaluated_inputs: dict[str, Any] = field(
        default_factory=dict, metadata={"customer_visible": False}
    )
    checked_rules: list[str] = field(
        default_factory=list, metadata={"customer_visible": False}
    )


@dataclass
class CommercialRuleResult:
    rule_id: str
    name: str
    status: str
    message: str


@dataclass
class CommercialValidationResult:
    status: str = "not_checked"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approval_required: bool = False
    approval_reasons: list[str] = field(default_factory=list)
    rule_results: list[CommercialRuleResult] = field(default_factory=list)
    evaluated_rules: list[str] = field(
        default_factory=list, metadata={"customer_visible": False}
    )


@dataclass
class ApprovalRecord:
    status: ApprovalStatus = ApprovalStatus.NOT_READY
    actor: str = ""
    actor_role: str = ""
    action: str = ""
    reason: str = ""
    original_price: float | None = None
    final_price: float | None = None
    timestamp: datetime | None = None
    triggered_rule_ids: list[str] = field(default_factory=list)
    override_justification: str = ""
    action_id: str = ""
    reminder_due_at: datetime | None = None

    def __post_init__(self) -> None:
        self.status = ApprovalStatus(self.status)


@dataclass
class EmailOutput:
    email_type: str
    subject: str
    body: str


@dataclass
class DocumentOutput:
    filename: str
    mime_type: str
    bytes_data: bytes


@dataclass
class AuditEvent:
    event_type: str
    actor: str
    timestamp: datetime
    quotation_id: str = ""
    before_state: str = ""
    after_state: str = ""
    changed_fields: list[str] = field(default_factory=list)
    reason: str = ""
    triggered_rule_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CombinedDecision:
    status: str
    summary: str
    triggered_rule_ids: list[str]
    approval_required: bool
    recommended_next_action: str


@dataclass
class QuotationWorkflowState:
    draft: QuotationDraft
    product_recommendation: Any | None = None
    pricing_result: PricingResult | None = None
    technical_validation: TechnicalValidationResult | None = None
    commercial_validation: CommercialValidationResult | None = field(
        default=None, metadata={"customer_visible": False}
    )
    combined_decision: CombinedDecision | None = field(
        default=None, metadata={"customer_visible": False}
    )
    validation_stale: bool = True
    approval: ApprovalRecord = field(
        default_factory=ApprovalRecord, metadata={"customer_visible": False}
    )
    internal_email: EmailOutput | None = field(
        default=None, metadata={"customer_visible": False}
    )
    customer_email: EmailOutput | None = None
    audit_events: list[AuditEvent] = field(
        default_factory=list, metadata={"customer_visible": False}
    )
    current_stage: WorkflowStage = WorkflowStage.DRAFT

    def __post_init__(self) -> None:
        self.current_stage = WorkflowStage(self.current_stage)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_customer_dict(self) -> dict[str, Any]:
        return to_customer_jsonable(self)
