"""Shared Phase 6 helpers: users, principals and approvable workflow states."""

from __future__ import annotations

from decimal import Decimal

from app.auth import LocalPasswordAuthenticationProvider, Role
from app.commercial_policy import INTERNAL_MVP_PROVISIONAL_POLICY, policy_key
from app.quotation_models import (
    CombinedDecision,
    CommercialValidationResult,
    PricingResult,
    QuotationPricingAnalysis,
    TechnicalValidationResult,
    WorkflowStage,
)

POLICY_VERSION_ID = policy_key(INTERNAL_MVP_PROVISIONAL_POLICY)
THRESHOLD = str(INTERNAL_MVP_PROVISIONAL_POLICY.pass_margin_threshold_percent)

PASSWORD = "correct-horse-battery"


def create_user(
    provider: LocalPasswordAuthenticationProvider,
    username: str,
    role: Role,
    *,
    password: str = PASSWORD,
    email: str | None = None,
):
    provider.create_user(
        username=username,
        password=password,
        roles=(role,),
        display_name=username.title(),
        email=email if email is not None else f"{username}@internal.invalid",
    )
    return provider.authenticate(username, password)


def make_decided_state(
    state,
    *,
    status: str,
    margin: str | None,
    pricing_run_id: str = "PR-1",
    validation_run_id: str = "TV-1",
) -> None:
    """Attach a deterministic Phase 5 style result set to a workflow state."""

    state.pricing_result = PricingResult(
        selected_product_ids=["SYN-MAIN-1"],
        currency="USD",
        recommended_unit_price=100000.0,
        total_price=100000.0,
        confidence_label="high",
    )
    state.quotation_pricing = QuotationPricingAnalysis(
        quotation_id=state.draft.quotation_id,
        pricing_run_id=pricing_run_id,
        currency="USD",
        total_revenue="100000.00",
        total_cost="60000.00",
        gross_margin_percent=margin,
        margin_status="available" if margin is not None else "unavailable",
    )
    state.technical_validation = TechnicalValidationResult(
        status="pass" if status != "blocked" else "invalid",
        errors=[] if status != "blocked" else ["incompatible_detector"],
    )
    state.commercial_validation = CommercialValidationResult(
        status=status,
        approval_required=status == "review_required",
    )
    state.combined_decision = CombinedDecision(
        status=status,
        summary=f"Deterministic {status} decision.",
        triggered_rule_ids=["COMM-MARGIN-001"]
        if status == "pass"
        else ["COMM-MARGIN-002"],
        approval_required=status == "review_required",
        recommended_next_action="Route to human approval.",
        policy_version_id=POLICY_VERSION_ID,
        policy_name=INTERNAL_MVP_PROVISIONAL_POLICY.policy_name,
        evaluated_margin_percent=margin,
        threshold_percent=THRESHOLD,
        pricing_run_id=pricing_run_id,
        technical_validation_run_id=validation_run_id,
    )
    state.validation_stale = False
    state.current_stage = WorkflowStage.ANALYSED


def decimal_margin(value: str) -> Decimal:
    return Decimal(value)
