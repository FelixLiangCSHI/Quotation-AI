"""Persistence tests: creation, reload, line items, versioning, lifecycle."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.dto import LineItemDTO, LineItemType
from app.domain.workflow_state_codec import (
    STATE_SCHEMA_VERSION,
    dump_workflow_state,
    load_workflow_state,
)
from app.quotation_models import ApprovalStatus, WorkflowStage
from app.repositories.interfaces import (
    QuotationNotFoundError,
    QuotationVersionConflictError,
)
from app.workflow_state import initialize_workflow_state


def test_create_quotation_persists_core_fields(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0001")

    assert loaded.record.id > 0
    assert loaded.record.quotation_id == "Q-TEST-0001"
    assert loaded.record.version == 1
    assert loaded.record.status == WorkflowStage.DRAFT.value
    assert loaded.record.approval_status == ApprovalStatus.NOT_READY.value
    assert loaded.record.created_at is not None
    assert loaded.record.updated_at is not None
    assert loaded.record.state_document["schema_version"] == STATE_SCHEMA_VERSION


def test_quotation_id_is_distinct_from_primary_key(service):
    first = service.create_quotation(quotation_id="Q-TEST-A")
    second = service.create_quotation(quotation_id="Q-TEST-B")

    assert first.record.id != second.record.id
    assert first.record.quotation_id != second.record.quotation_id


def test_reload_restores_workflow_state(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0002")
    loaded.state.draft.customer_name = "Synthetic Hospital"
    loaded.state.draft.region = "us"
    loaded.state.draft.incoterm = "DDP"
    loaded.state.draft.delivery_location = "Springfield"
    loaded.state.draft.currency = "EUR"
    service.save_state(loaded, changed_fields=("customer_name",))

    reopened = service.load_quotation("Q-TEST-0002")

    assert reopened.record.customer_name == "Synthetic Hospital"
    assert reopened.record.region == "us"
    assert reopened.record.incoterm == "DDP"
    assert reopened.record.delivery_location == "Springfield"
    assert reopened.record.currency == "EUR"
    assert reopened.state.draft.customer_name == "Synthetic Hospital"
    assert reopened.record.version == 2


def test_state_document_round_trips_exactly(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0003")
    original = dump_workflow_state(loaded.state)

    restored = load_workflow_state(original)

    assert dump_workflow_state(restored) == original


def test_unknown_quotation_raises(service):
    with pytest.raises(QuotationNotFoundError):
        service.load_quotation("Q-DOES-NOT-EXIST")


def test_quotation_supports_three_independent_line_items(
    service, three_line_items
):
    loaded = service.create_quotation(quotation_id="Q-TEST-0004")
    updated = service.replace_line_items(loaded, three_line_items)

    reopened = service.load_quotation("Q-TEST-0004")
    items = reopened.record.line_items

    assert len(items) == 3
    assert [item.position for item in items] == [0, 1, 2]
    assert [item.item_type for item in items] == [
        LineItemType.MAIN_PRODUCT,
        LineItemType.ACCESSORY,
        LineItemType.SERVICE,
    ]
    assert items[1].quantity == 2
    assert items[1].proposed_unit_price == Decimal("2500.5000")
    assert updated.record.version == 2


def test_line_item_totals_use_exact_decimal_arithmetic(
    service, three_line_items
):
    loaded = service.create_quotation(quotation_id="Q-TEST-0005")
    service.replace_line_items(loaded, three_line_items)

    reopened = service.load_quotation("Q-TEST-0005")

    # 100000.00 + (2 x 2500.50) + 7500.25
    assert reopened.record.line_item_total == Decimal("112501.2500")


def test_all_line_item_types_persist(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0006")
    items = tuple(
        LineItemDTO(
            position=index,
            item_type=item_type,
            product_id=f"SYN-{index}",
            customer_description=f"Synthetic {item_type.value}",
            proposed_unit_price=Decimal("10.00"),
        )
        for index, item_type in enumerate(LineItemType)
    )
    service.replace_line_items(loaded, items)

    reopened = service.load_quotation("Q-TEST-0006")

    assert {item.item_type for item in reopened.record.line_items} == set(
        LineItemType
    )


def test_approved_unit_price_overrides_proposed_price(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0007")
    service.replace_line_items(
        loaded,
        (
            LineItemDTO(
                position=0,
                product_id="SYN-1",
                quantity=3,
                proposed_unit_price=Decimal("100.00"),
                approved_unit_price=Decimal("90.00"),
            ),
        ),
    )

    item = service.load_quotation("Q-TEST-0007").record.line_items[0]

    assert item.effective_unit_price == Decimal("90.0000")
    assert item.extended_price == Decimal("270.0000")


def test_optional_line_items_are_excluded_from_the_total(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0008")
    service.replace_line_items(
        loaded,
        (
            LineItemDTO(
                position=0,
                product_id="SYN-1",
                proposed_unit_price=Decimal("100.00"),
            ),
            LineItemDTO(
                position=1,
                product_id="SYN-2",
                proposed_unit_price=Decimal("999.00"),
                is_optional=True,
            ),
        ),
    )

    record = service.load_quotation("Q-TEST-0008").record

    assert record.line_item_total == Decimal("100.0000")


def test_duplicate_line_item_positions_are_rejected(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0009")
    duplicates = (
        LineItemDTO(position=0, product_id="SYN-1"),
        LineItemDTO(position=0, product_id="SYN-2"),
    )

    with pytest.raises(ValueError):
        service.replace_line_items(loaded, duplicates)


def test_line_item_quantity_must_be_positive():
    with pytest.raises(ValueError):
        LineItemDTO(position=0, quantity=0)


def test_version_increments_on_every_write(service):
    loaded = service.create_quotation(quotation_id="Q-TEST-0010")
    assert loaded.record.version == 1

    loaded = service.save_state(loaded)
    assert loaded.record.version == 2

    loaded = service.save_state(loaded)
    assert loaded.record.version == 3


def test_concurrent_edit_raises_version_conflict(service):
    service.create_quotation(quotation_id="Q-TEST-0011")

    first = service.load_quotation("Q-TEST-0011")
    second = service.load_quotation("Q-TEST-0011")

    first.state.draft.customer_name = "First writer"
    service.save_state(first)

    # The second user still holds version 1 and must not silently overwrite.
    second.state.draft.customer_name = "Second writer"
    with pytest.raises(QuotationVersionConflictError) as error:
        service.save_state(second)

    assert error.value.expected_version == 1
    assert error.value.actual_version == 2

    assert service.load_quotation("Q-TEST-0011").record.customer_name == (
        "First writer"
    )


def test_version_conflict_also_guards_line_item_writes(
    service, three_line_items
):
    service.create_quotation(quotation_id="Q-TEST-0012")
    stale = service.load_quotation("Q-TEST-0012")
    fresh = service.load_quotation("Q-TEST-0012")

    service.save_state(fresh)

    with pytest.raises(QuotationVersionConflictError):
        service.replace_line_items(stale, three_line_items)


def test_quotation_can_be_closed_reopened_and_recovered(service):
    created = service.create_quotation(quotation_id="Q-TEST-0013")
    created.state.draft.customer_name = "Synthetic Clinic"
    saved = service.save_state(created)

    closed = service.close_quotation(saved)
    assert closed.record.is_closed is True

    # Recover it from the database exactly as a new session would.
    recovered = service.load_quotation("Q-TEST-0013")
    assert recovered.record.is_closed is True
    assert recovered.state.draft.customer_name == "Synthetic Clinic"

    reopened = service.reopen_quotation(recovered)
    assert reopened.record.is_closed is False
    assert service.load_quotation("Q-TEST-0013").record.is_closed is False


def test_list_summaries_can_exclude_closed_quotations(service):
    open_quotation = service.create_quotation(quotation_id="Q-TEST-0014")
    closed_quotation = service.create_quotation(quotation_id="Q-TEST-0015")
    service.close_quotation(closed_quotation)

    all_summaries = service.list_quotations()
    open_only = service.list_quotations(include_closed=False)

    assert {item.quotation_id for item in all_summaries} == {
        "Q-TEST-0014",
        "Q-TEST-0015",
    }
    assert {item.quotation_id for item in open_only} == {
        open_quotation.record.quotation_id
    }


def test_existing_workflow_state_helpers_still_produce_persistable_state(
    service,
):
    state = initialize_workflow_state(quotation_id="Q-TEST-0016")
    loaded = service.create_quotation(state=state)

    reopened = service.load_quotation("Q-TEST-0016")

    assert loaded.record.quotation_id == "Q-TEST-0016"
    assert reopened.state.current_stage is WorkflowStage.DRAFT
    # The in-memory bootstrap audit event survives the round trip.
    assert any(
        event.event_type == "draft_created"
        for event in reopened.state.audit_events
    )
