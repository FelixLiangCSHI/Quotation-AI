"""Typed domain DTOs exposed to the UI and service callers.

The UI never receives a SQLAlchemy model. Services translate ORM rows into
these frozen dataclasses, which carry no database session and cannot lazily
load. Customer-facing serialisation reuses the existing
``customer_visible`` metadata convention and ``CUSTOMER_PROHIBITED_FIELDS``
denylist, so the established customer/internal separation is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.serialization import to_customer_jsonable, to_jsonable


class LineItemType(str, Enum):
    """The commercial category of a quotation line item."""

    MAIN_PRODUCT = "main_product"
    ACCESSORY = "accessory"
    SERVICE = "service"
    INSTALLATION = "installation"
    WARRANTY = "warranty"
    FREIGHT = "freight"
    COMMERCIAL_ADDITION = "commercial_addition"


#: Types that represent a physical or configurable product rather than a
#: commercial addition. Used by pricing and validation call sites.
PRODUCT_LINE_ITEM_TYPES = frozenset(
    {LineItemType.MAIN_PRODUCT, LineItemType.ACCESSORY}
)


@dataclass(frozen=True)
class UserDTO:
    id: int
    username: str
    display_name: str = ""
    email: str = field(default="", metadata={"customer_visible": False})
    roles: tuple[str, ...] = field(
        default=(), metadata={"customer_visible": False}
    )
    is_active: bool = True


@dataclass(frozen=True)
class LineItemDTO:
    """A quotation line item as seen by callers outside the persistence layer."""

    position: int
    item_type: LineItemType = LineItemType.MAIN_PRODUCT
    product_id: str = ""
    customer_description: str = ""
    internal_description: str = field(
        default="", metadata={"customer_visible": False}
    )
    quantity: int = 1
    currency: str = "USD"
    proposed_unit_price: Decimal | None = None
    approved_unit_price: Decimal | None = None
    list_unit_price: Decimal | None = None
    discount_percent: Decimal | None = None
    is_optional: bool = False
    id: int | None = field(default=None, metadata={"customer_visible": False})

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_type", LineItemType(self.item_type))
        if self.quantity < 1:
            raise ValueError("Line item quantity must be at least 1.")
        if self.position < 0:
            raise ValueError("Line item position cannot be negative.")

    @property
    def effective_unit_price(self) -> Decimal | None:
        """The approved price when present, otherwise the proposed price.

        The approved price always wins; a proposed price never overrides an
        approver's decision.
        """

        if self.approved_unit_price is not None:
            return self.approved_unit_price
        return self.proposed_unit_price

    @property
    def extended_price(self) -> Decimal | None:
        unit_price = self.effective_unit_price
        if unit_price is None:
            return None
        return unit_price * Decimal(self.quantity)


@dataclass(frozen=True)
class QuotationSummaryDTO:
    """Lightweight projection for listings and approval inboxes."""

    id: int
    quotation_id: str
    customer_name: str
    region: str
    currency: str
    status: str
    approval_status: str
    version: int
    is_closed: bool
    owner_user_id: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AuditEventDTO:
    event_type: str
    actor: str
    occurred_at: datetime
    quotation_reference: str = ""
    before_state: str = ""
    after_state: str = ""
    changed_fields: tuple[str, ...] = ()
    reason: str = ""
    triggered_rule_ids: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    actor_role: str = ""
    quotation_version: int = 0
    policy_version_id: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class ApprovalTaskDTO:
    """A persistent approval task as exposed to services and the UI."""

    id: int
    task_reference: str
    quotation_reference: str
    quotation_version: int
    decision_status: str
    status: str
    assigned_user_id: int | None = None
    assigned_approver_name: str = ""
    assigned_approver_role: str = ""
    submitted_by_user_id: int | None = None
    submitted_at: datetime | None = None
    reminder_due_at: datetime | None = None
    completed_at: datetime | None = None
    policy_version_id: str = ""
    pricing_run_id: str = ""
    validation_run_id: str = ""
    decision: str = ""
    reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "pending_review"


@dataclass(frozen=True)
class QuotationDTO:
    """The full quotation as exposed to the UI and service callers."""

    id: int
    quotation_id: str
    customer_name: str = ""
    customer_type: str = ""
    region: str = ""
    currency: str = "USD"
    incoterm: str = ""
    delivery_location: str = ""
    status: str = "draft"
    approval_status: str = field(
        default="not_ready", metadata={"customer_visible": False}
    )
    is_closed: bool = field(default=False, metadata={"customer_visible": False})
    version: int = field(default=1, metadata={"customer_visible": False})
    owner_user_id: int | None = field(
        default=None, metadata={"customer_visible": False}
    )
    pricing_data_version_id: int | None = field(
        default=None, metadata={"customer_visible": False}
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None
    line_items: tuple[LineItemDTO, ...] = ()
    audit_events: tuple[AuditEventDTO, ...] = field(
        default=(), metadata={"customer_visible": False}
    )
    # Serialised QuotationWorkflowState, kept for deterministic engines.
    state_document: dict[str, Any] = field(
        default_factory=dict, metadata={"customer_visible": False}
    )

    @property
    def summary(self) -> QuotationSummaryDTO:
        if self.created_at is None or self.updated_at is None:
            raise ValueError("Quotation timestamps are not populated.")
        return QuotationSummaryDTO(
            id=self.id,
            quotation_id=self.quotation_id,
            customer_name=self.customer_name,
            region=self.region,
            currency=self.currency,
            status=self.status,
            approval_status=self.approval_status,
            version=self.version,
            is_closed=self.is_closed,
            owner_user_id=self.owner_user_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def line_items_of_type(
        self, *item_types: LineItemType
    ) -> tuple[LineItemDTO, ...]:
        wanted = {LineItemType(item) for item in item_types}
        return tuple(item for item in self.line_items if item.item_type in wanted)

    @property
    def line_item_total(self) -> Decimal | None:
        """Sum of extended prices, or ``None`` if any priced line is missing.

        Optional lines are excluded because they are not part of the committed
        commercial offer.
        """

        included = [item for item in self.line_items if not item.is_optional]
        if not included:
            return None
        total = Decimal("0")
        for item in included:
            extended = item.extended_price
            if extended is None:
                return None
            total += extended
        return total

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_customer_dict(self) -> dict[str, Any]:
        return to_customer_jsonable(self)
