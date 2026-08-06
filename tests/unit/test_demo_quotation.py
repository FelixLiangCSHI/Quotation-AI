"""Unit tests for the simplified demo quotation logic."""

from __future__ import annotations

import pytest

from app.demo_assistant import DemoQuotationAssistant
from app.demo_quotation import (
    DISCOUNT_APPROVAL_THRESHOLD,
    approval_status,
    build_approval_description,
    build_customer_pdf,
    build_quotation_excel,
    build_quotation_lines,
    compute_totals,
    normalize_configuration,
    parse_discount_rate,
)
from app.natural_language import parse_quote_request


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30%", 0.30),
        ("35%", 0.35),
        ("Apply a 30 percent discount", 0.30),
        ("Give the customer 40% off", 0.40),
        ("折扣 40%", 0.40),
        ("2 units for Singapore", None),
        ("", None),
    ],
)
def test_parse_discount_rate(text: str, expected: float | None) -> None:
    result = parse_discount_rate(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (0.0, "AUTO_APPROVED"),
        (0.30, "AUTO_APPROVED"),
        (0.3499, "AUTO_APPROVED"),
        (DISCOUNT_APPROVAL_THRESHOLD, "AUTO_APPROVED"),
        (0.3501, "MANAGER_APPROVAL_REQUIRED"),
        (0.40, "MANAGER_APPROVAL_REQUIRED"),
    ],
)
def test_approval_boundary_is_inclusive(rate: float, expected: str) -> None:
    assert approval_status(rate) == expected


def _configuration() -> dict:
    return {
        "customer_name": "Demo Hospital",
        "region": "Singapore",
        "currency": "USD",
        "main_product": "DEMO-FMT-100",
        "main_product_description": "Synthetic FMT digital X-ray system",
        "quantity": 2,
        "accessories": [
            {
                "product_id": "DEMO-DETECTOR-43",
                "description": "Synthetic Focus 43C detector",
                "quantity": 1,
            }
        ],
        "configuration_description": "2 x system",
        "discount_rate": 0.30,
    }


def test_build_quotation_lines_applies_discount() -> None:
    lines = build_quotation_lines(_configuration())

    assert [line["product_code"] for line in lines] == [
        "DEMO-FMT-100",
        "DEMO-DETECTOR-43",
    ]
    assert lines[0]["quotation_unit_price"] == pytest.approx(70000.0)
    assert lines[0]["list_line_total"] == pytest.approx(200000.0)
    assert lines[0]["quotation_line_total"] == pytest.approx(140000.0)


def test_compute_totals_derives_discount_rate() -> None:
    lines = build_quotation_lines(_configuration(), discount_rate=0.40)
    totals = compute_totals(lines)

    assert totals["discount_rate"] == pytest.approx(0.40)
    assert totals["quotation_total"] == pytest.approx(
        totals["list_total"] * 0.60
    )
    assert approval_status(totals["discount_rate"]) == (
        "MANAGER_APPROVAL_REQUIRED"
    )


def test_compute_totals_reacts_to_edited_lines() -> None:
    lines = build_quotation_lines(_configuration(), discount_rate=0.30)
    lines[0]["quotation_unit_price"] = 50000.0

    totals = compute_totals(lines)

    assert totals["discount_rate"] > DISCOUNT_APPROVAL_THRESHOLD


def test_compute_totals_with_zero_list_total() -> None:
    totals = compute_totals(
        [
            {
                "product_code": "X",
                "description": "X",
                "quantity": 0,
                "list_unit_price": 0.0,
                "quotation_unit_price": 0.0,
            }
        ]
    )

    assert totals["discount_rate"] == 0.0


def test_normalize_configuration_is_multi_turn() -> None:
    first = normalize_configuration(
        parse_quote_request("2 units for Singapore in USD"), None, None
    )
    assert first["quantity"] == 2
    assert first["region"] == "Singapore"
    assert first["currency"] == "USD"

    second = normalize_configuration(
        parse_quote_request("Apply a 40 percent discount"), None, first
    )
    assert second["quantity"] == 2
    assert second["region"] == "Singapore"
    assert second["discount_rate"] == pytest.approx(0.40)


def test_approval_description_mentions_threshold_and_rate() -> None:
    config = _configuration()
    totals = compute_totals(build_quotation_lines(config, discount_rate=0.40))

    text = build_approval_description("Q-DEMO-1", config, totals)

    assert "Q-DEMO-1" in text
    assert "Demo Hospital" in text
    assert "40.0%" in text
    assert "35.0%" in text
    assert "Margin" not in text
    assert "Cost" not in text


def test_excel_and_pdf_are_generated_in_memory() -> None:
    config = _configuration()
    lines = build_quotation_lines(config)
    totals = compute_totals(lines)

    workbook = build_quotation_excel("Q-DEMO-1", config, lines, totals)
    approval_workbook = build_quotation_excel(
        "Q-DEMO-1", config, lines, totals, internal=True
    )
    pdf = build_customer_pdf("Q-DEMO-1", config, lines, totals)

    assert workbook[:2] == b"PK"
    assert approval_workbook[:2] == b"PK"
    assert pdf[:4] == b"%PDF"


def test_assistant_conversation_reaches_a_quotation() -> None:
    assistant = DemoQuotationAssistant()

    first = assistant.handle_message(
        "Quote a digital floor mounted X-ray system for Demo Hospital, "
        "2 units, Singapore, USD"
    )
    assert first.configuration["main_product"]
    assert first.configuration_ready
    assert "discount rate" in first.reply.lower()

    second = assistant.handle_message("40%", first.configuration)
    assert second.discount_rate == pytest.approx(0.40)
    # A discount-only turn must not change the confirmed configuration.
    assert second.configuration["main_product"] == (
        first.configuration["main_product"]
    )
    assert second.configuration["quantity"] == first.configuration["quantity"]
    assert second.configuration["accessories"] == (
        first.configuration["accessories"]
    )

    lines = build_quotation_lines(
        second.configuration, second.discount_rate
    )
    totals = compute_totals(lines)
    assert approval_status(totals["discount_rate"]) == (
        "MANAGER_APPROVAL_REQUIRED"
    )
