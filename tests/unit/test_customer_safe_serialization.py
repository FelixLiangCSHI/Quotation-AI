"""Customer-safe serialisation of the persisted domain DTOs.

The customer/internal separation established in the demo must survive the move
to persistence. Internal-only fields must never appear in customer output.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.config import CUSTOMER_PROHIBITED_FIELDS
from app.domain.dto import LineItemDTO, LineItemType, QuotationDTO
from app.quotation_models import utc_now

#: Substrings that must never appear anywhere in a customer-facing payload.
INTERNAL_TOKENS = (
    "cost basis",
    "internal only",
    "minimum price",
    "margin floor",
)


def _quotation() -> QuotationDTO:
    now = utc_now()
    return QuotationDTO(
        id=1,
        quotation_id="Q-CUST-0001",
        customer_name="Synthetic Hospital",
        region="us",
        currency="USD",
        incoterm="DDP",
        delivery_location="Springfield",
        status="approved",
        approval_status="approved",
        version=4,
        owner_user_id=7,
        pricing_data_version_id=3,
        created_at=now,
        updated_at=now,
        line_items=(
            LineItemDTO(
                id=11,
                position=0,
                item_type=LineItemType.MAIN_PRODUCT,
                product_id="SYN-MAIN-1",
                customer_description="Synthetic imaging system",
                internal_description="cost basis 61200; minimum price 82000",
                quantity=1,
                proposed_unit_price=Decimal("100000.00"),
            ),
        ),
        state_document={"minimum_price": 82000, "estimated_cost": 61200},
    )


def test_customer_payload_excludes_internal_line_item_description():
    payload = _quotation().to_customer_dict()

    item = payload["line_items"][0]
    assert item["customer_description"] == "Synthetic imaging system"
    assert "internal_description" not in item


def test_customer_payload_excludes_internal_quotation_fields():
    payload = _quotation().to_customer_dict()

    for name in (
        "approval_status",
        "is_closed",
        "version",
        "owner_user_id",
        "pricing_data_version_id",
        "audit_events",
        "state_document",
    ):
        assert name not in payload, f"{name} leaked into customer output"


def test_customer_payload_keeps_customer_relevant_fields():
    payload = _quotation().to_customer_dict()

    assert payload["quotation_id"] == "Q-CUST-0001"
    assert payload["customer_name"] == "Synthetic Hospital"
    assert payload["currency"] == "USD"
    assert payload["incoterm"] == "DDP"
    assert payload["delivery_location"] == "Springfield"


def test_no_internal_token_appears_anywhere_in_customer_output():
    serialized = json.dumps(_quotation().to_customer_dict()).casefold()

    for token in INTERNAL_TOKENS:
        assert token not in serialized, f"{token!r} leaked into customer output"


def test_prohibited_field_names_never_appear_as_customer_keys():
    payload = _quotation().to_customer_dict()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert str(key).casefold() not in CUSTOMER_PROHIBITED_FIELDS
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)


def test_internal_payload_retains_internal_fields():
    payload = _quotation().to_dict()

    assert payload["version"] == 4
    assert payload["owner_user_id"] == 7
    assert payload["state_document"]["minimum_price"] == 82000
    assert (
        payload["line_items"][0]["internal_description"]
        == "cost basis 61200; minimum price 82000"
    )


def test_user_dto_hides_contact_details_and_roles_from_customers():
    from app.domain.dto import UserDTO
    from app.serialization import to_customer_jsonable

    payload = to_customer_jsonable(
        UserDTO(
            id=1,
            username="internal.user",
            display_name="Internal User",
            email="internal.user@example.invalid",
            roles=("Sales Manager",),
        )
    )

    assert "email" not in payload
    assert "roles" not in payload
