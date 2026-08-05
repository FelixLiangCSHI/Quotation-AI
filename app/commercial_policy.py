"""Versioned commercial policy model (Phase 5).

The commercial margin gate is deterministic and is driven entirely by a
versioned :class:`CommercialPolicyVersion` record. The threshold value lives in
exactly one place - the policy record - so that it can be replaced later
without touching calculation code.

The initial policy is a *provisional internal-MVP assumption*: the formal
company approval standard has not been supplied yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Iterable, Sequence


class MissingCostPolicy(str, Enum):
    """How a revenue line without a trusted cost basis must be handled."""

    #: Any revenue-bearing line without a trusted cost basis blocks the
    #: quotation. A missing cost is never silently treated as zero.
    BLOCK = "block_on_missing_revenue_line_cost"


class CurrencyPolicy(str, Enum):
    """How mixed-currency quotations must be handled."""

    #: Every monetary value must already be expressed in the single quotation
    #: currency. No live FX conversion exists in this phase.
    SINGLE_CURRENCY_REQUIRED = "single_normalised_currency_required"


class PolicyStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DRAFT = "draft"


class ComparisonOperator(str, Enum):
    GREATER_THAN = "greater_than"


@dataclass(frozen=True)
class CommercialPolicyVersion:
    """An immutable, auditable commercial policy version."""

    policy_id: str
    policy_name: str
    version: str
    effective_from: date
    pass_margin_threshold_percent: Decimal
    missing_cost_policy: MissingCostPolicy = MissingCostPolicy.BLOCK
    currency_policy: CurrencyPolicy = CurrencyPolicy.SINGLE_CURRENCY_REQUIRED
    status: PolicyStatus = PolicyStatus.ACTIVE
    created_by: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    effective_to: date | None = None
    approved_by: str | None = None
    comparison_operator: ComparisonOperator = ComparisonOperator.GREATER_THAN
    #: Categories for which an additive optional component may legitimately
    #: carry a zero cost. Everything else must supply a trusted cost basis.
    zero_cost_permitted_categories: tuple[str, ...] = ()
    #: A transfer price may only stand in for COGS when a policy says so.
    allow_transfer_price_as_cogs: bool = False
    notes: str = ""

    def is_effective_on(self, moment: date) -> bool:
        if moment < self.effective_from:
            return False
        return self.effective_to is None or moment <= self.effective_to

    def permits_zero_cost(self, category: str) -> bool:
        return category in self.zero_cost_permitted_categories


#: Provisional internal-MVP policy. ``35.0`` appears here and nowhere else.
INTERNAL_MVP_PROVISIONAL_POLICY = CommercialPolicyVersion(
    policy_id="POL-MARGIN-MVP-001",
    policy_name="Internal MVP Provisional Margin Policy",
    version="1.0.0",
    effective_from=date(2024, 1, 1),
    pass_margin_threshold_percent=Decimal("35.0"),
    missing_cost_policy=MissingCostPolicy.BLOCK,
    currency_policy=CurrencyPolicy.SINGLE_CURRENCY_REQUIRED,
    status=PolicyStatus.ACTIVE,
    created_by="internal-mvp",
    approved_by=None,
    zero_cost_permitted_categories=("commercial_addition",),
    allow_transfer_price_as_cogs=False,
    notes=(
        "Provisional internal-MVP assumption. Gross margin strictly greater "
        "than the threshold passes the commercial margin gate; a margin equal "
        "to or below the threshold requires human approval. The formal company "
        "approval standard has not been supplied and must replace this policy."
    ),
)


class CommercialPolicyRegistry:
    """Holds every known policy version and resolves the active one.

    Historical decisions store a ``policy_version_id``. Registering a new
    active policy never mutates a decision that has already been recorded.
    """

    def __init__(
        self, policies: Iterable[CommercialPolicyVersion] | None = None
    ) -> None:
        source = (
            tuple(policies)
            if policies is not None
            else (INTERNAL_MVP_PROVISIONAL_POLICY,)
        )
        self._policies: dict[str, CommercialPolicyVersion] = {}
        for policy in source:
            self.register(policy)

    @property
    def policies(self) -> Sequence[CommercialPolicyVersion]:
        return tuple(self._policies.values())

    def register(self, policy: CommercialPolicyVersion) -> None:
        key = policy_key(policy)
        if key in self._policies:
            raise ValueError(f"Policy version already registered: {key}")
        self._policies[key] = policy

    def get(self, policy_version_id: str) -> CommercialPolicyVersion:
        try:
            return self._policies[policy_version_id]
        except KeyError as error:
            raise KeyError(
                f"Unknown commercial policy version: {policy_version_id}"
            ) from error

    def active_policy(
        self, *, on: date | None = None
    ) -> CommercialPolicyVersion:
        moment = on or datetime.now(timezone.utc).date()
        candidates = [
            policy
            for policy in self._policies.values()
            if policy.status is PolicyStatus.ACTIVE
            and policy.is_effective_on(moment)
        ]
        if not candidates:
            raise LookupError(
                "No active commercial policy version is effective on "
                f"{moment.isoformat()}."
            )
        candidates.sort(key=lambda policy: policy.effective_from)
        return candidates[-1]


def policy_key(policy: CommercialPolicyVersion) -> str:
    return f"{policy.policy_id}@{policy.version}"


DEFAULT_POLICY_REGISTRY = CommercialPolicyRegistry()


def active_commercial_policy(
    registry: CommercialPolicyRegistry | None = None,
    *,
    on: date | None = None,
) -> CommercialPolicyVersion:
    return (registry or DEFAULT_POLICY_REGISTRY).active_policy(on=on)
