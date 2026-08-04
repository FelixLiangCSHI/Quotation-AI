"""Phase 4 persistence tests: draft save/resume, duplicate and clone."""

from __future__ import annotations

import pytest

from app.line_items import add_line_item
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    LineItemCategory,
    PricingResult,
)
from app.repositories.interfaces import QuotationNotFoundError
from app.workflow_state import initialize_workflow_state
from tests.fixtures.catalog_fixtures import synthetic_rule_engine


@pytest.fixture()
def rule_engine():
    return synthetic_rule_engine()


def _multi_line_state(rule_engine):
    state = initialize_workflow_state()
    draft = state.draft
    draft.customer_name = "Synthetic Hospital"
    draft.region = "china"
    draft.product_query = "synthetic imaging system"
    draft.quantity = 2
    draft.currency = "USD"
    draft.incoterm = "DAP"
    draft.delivery_location = "Synthetic City"
    draft.intended_use = "general radiography"
    draft.requested_services = ["installation"]
    draft.missing_fields = []

    add_line_item(
        state,
        product_id="SYN-MAIN-1",
        description="Synthetic imaging system console",
        category=LineItemCategory.MAIN_PRODUCT,
        unit_price=100000.0,
        engine=rule_engine,
    )
    add_line_item(
        state,
        product_id="SYN-ACC-1",
        description="Synthetic detector grid",
        category=LineItemCategory.ACCESSORY,
        quantity=2,
        unit_price=2500.0,
        engine=rule_engine,
    )
    add_line_item(
        state,
        product_id="SYN-ACC-2",
        description="Synthetic wall stand",
        category=LineItemCategory.ACCESSORY,
        unit_price=1500.0,
        engine=rule_engine,
    )
    add_line_item(
        state,
        description="Installation and commissioning",
        category=LineItemCategory.SERVICE,
        unit_price=7500.0,
        engine=rule_engine,
    )
    return state


def test_draft_save_and_resume_round_trip(service, rule_engine):
    state = _multi_line_state(rule_engine)
    saved = service.save_draft(state, actor="tester")

    resumed = service.resume_draft(saved.quotation_id)
    assert resumed.state.draft.customer_name == "Synthetic Hospital"
    assert resumed.state.draft.intended_use == "general radiography"
    assert resumed.state.draft.requested_services == ["installation"]
    assert len(resumed.state.draft.line_items) == 4
    assert [item.quantity for item in resumed.state.draft.line_items] == [1, 2, 1, 1]
    assert len(resumed.record.line_items) == 4


def test_draft_save_is_idempotent_for_an_existing_quotation(service, rule_engine):
    state = _multi_line_state(rule_engine)
    first = service.save_draft(state)
    state.draft.delivery_location = "Second Synthetic City"
    second = service.save_draft(state)

    assert second.quotation_id == first.quotation_id
    assert (
        service.resume_draft(first.quotation_id).state.draft.delivery_location
        == "Second Synthetic City"
    )


def test_duplicate_quotation_drops_pricing_and_approval(service, rule_engine):
    state = _multi_line_state(rule_engine)
    state.pricing_result = PricingResult(recommended_unit_price=100.0)
    state.approval = ApprovalRecord(status=ApprovalStatus.APPROVED)
    saved = service.save_draft(state)

    copy = service.duplicate_quotation(saved.quotation_id)
    assert copy.quotation_id != saved.quotation_id
    assert copy.state.draft.customer_name == "Synthetic Hospital"
    assert len(copy.state.draft.line_items) == 4
    assert copy.state.pricing_result is None
    assert copy.state.approval.status is ApprovalStatus.NOT_READY
    assert copy.state.validation_stale is True

    # The source quotation is untouched.
    source = service.resume_draft(saved.quotation_id)
    assert source.state.approval.status is ApprovalStatus.APPROVED


def test_clone_as_new_version_keeps_the_audit_lineage(service, rule_engine):
    state = _multi_line_state(rule_engine)
    saved = service.save_draft(state)

    clone = service.clone_as_new_version(saved.quotation_id)
    assert clone.quotation_id != saved.quotation_id
    assert len(clone.state.audit_events) > len(state.audit_events)
    event_types = [
        event.event_type for event in service.get_audit_trail(clone.quotation_id)
    ]
    assert "quotation_cloned_as_new_version" in event_types


def test_resume_of_an_unknown_quotation_raises(service):
    with pytest.raises(QuotationNotFoundError):
        service.resume_draft("Q-UNKNOWN")
