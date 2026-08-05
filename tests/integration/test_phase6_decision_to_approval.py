"""Phase 6: the real Phase 5 margin gate drives the approval workflow."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.approval_workflow import InvalidApprovalTransitionError
from app.margin_gate import (
    STATUS_BLOCKED,
    STATUS_PASS,
    STATUS_REVIEW_REQUIRED,
    evaluate_commercial_decision,
)
from app.quotation_models import (
    ApprovalStatus,
    LineItemCategory,
    PricingResult,
    QuotationLineItem,
    TechnicalValidationResult,
    WorkflowStage,
)
from app.quotation_pricing import analyse_quotation_pricing
from app.services.approval_service import allowed_actions_for

OVERRIDE_REASON = (
    "Fleet deal. I acknowledge the margin is equal to or below the "
    "configured policy threshold."
)


def _line(price: str, cost: str | None) -> QuotationLineItem:
    return QuotationLineItem(
        line_id="LI-1",
        product_id="SYN-MAIN-1",
        description="Synthetic imaging system",
        category=LineItemCategory.MAIN_PRODUCT,
        quantity=1,
        unit_price=price,
        estimated_unit_cost=cost,
        cost_source="test_fixture" if cost is not None else "",
    )


def _run_engines(state, margin: str | None, *, technical_ok: bool = True):
    """Run the real pricing analysis, technical validation and margin gate."""

    revenue = Decimal("1000")
    if margin is None:
        cost = None
    else:
        cost = str(revenue - revenue * Decimal(margin) / Decimal("100"))
    state.draft.line_items = [_line(str(revenue), cost)]
    state.draft.currency = "USD"
    pricing = analyse_quotation_pricing(state.draft)
    technical = TechnicalValidationResult(
        status="pass" if technical_ok else "invalid",
        errors=[] if technical_ok else ["incompatible_detector"],
    )
    decision = evaluate_commercial_decision(
        pricing, technical, technical_validation_run_id="TV-REAL-1"
    )
    state.quotation_pricing = pricing
    state.technical_validation = technical
    state.combined_decision = decision
    state.pricing_result = PricingResult(
        selected_product_ids=["SYN-MAIN-1"],
        currency="USD",
        recommended_unit_price=float(revenue),
        total_price=float(revenue),
        confidence_label="high",
    )
    state.validation_stale = False
    state.current_stage = WorkflowStage.ANALYSED
    return decision


def _submit(service, approval_service, people, quotation_id, margin, **kwargs):
    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    decision = _run_engines(loaded.state, margin, **kwargs)
    loaded = service.save_state(loaded, actor=people["sales"].username)
    task = approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people["manager"].user_id,
    )
    return decision, task


def test_margin_above_threshold_is_pass_and_needs_a_human(
    service, approval_service, people
):
    decision, task = _submit(
        service, approval_service, people, "Q6-E2E-PASS", "42.0"
    )

    assert decision.status == STATUS_PASS
    assert allowed_actions_for(decision.status) == (
        "approve",
        "request_revision",
    )
    assert task.status == "pending_review"

    completed = approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )
    assert completed.status == ApprovalStatus.APPROVED.value


def test_margin_exactly_at_threshold_takes_the_override_path(
    service, approval_service, people
):
    decision, task = _submit(
        service, approval_service, people, "Q6-E2E-35", "35.0"
    )

    assert decision.status == STATUS_REVIEW_REQUIRED
    with pytest.raises(InvalidApprovalTransitionError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )

    completed = approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="approve_with_override",
        reason=OVERRIDE_REASON,
        acknowledge_below_threshold=True,
    )
    assert completed.status == ApprovalStatus.APPROVED_WITH_OVERRIDE.value


def test_margin_below_threshold_can_be_sent_back_for_revision(
    service, approval_service, people
):
    decision, task = _submit(
        service, approval_service, people, "Q6-E2E-LOW", "18.0"
    )

    assert decision.status == STATUS_REVIEW_REQUIRED
    completed = approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="request_revision",
        reason="Improve the margin before this can be approved.",
    )
    assert completed.status == ApprovalStatus.REVISION_REQUESTED.value


def test_technical_incompatibility_blocks_every_approval(
    service, approval_service, people
):
    decision, task = _submit(
        service,
        approval_service,
        people,
        "Q6-E2E-BLOCK",
        "42.0",
        technical_ok=False,
    )

    assert decision.status == STATUS_BLOCKED
    assert allowed_actions_for(decision.status) == (
        "request_revision",
        "reject",
    )
    for action in ("approve", "approve_with_override"):
        with pytest.raises(InvalidApprovalTransitionError):
            approval_service.act(
                user=people["manager"],
                task_id=task.id,
                action=action,
                reason=OVERRIDE_REASON,
                acknowledge_below_threshold=True,
            )


def test_unavailable_trusted_margin_blocks(service, approval_service, people):
    decision, task = _submit(
        service, approval_service, people, "Q6-E2E-NOCOST", None
    )

    assert decision.status == STATUS_BLOCKED
    completed = approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="reject",
        reason="No trusted cost basis is available.",
    )
    assert completed.status == ApprovalStatus.REJECTED.value
