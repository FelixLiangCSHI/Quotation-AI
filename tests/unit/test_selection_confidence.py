"""Confidence-gated product selection and decision-tree aligned evidence."""

from __future__ import annotations

import pytest

from app.models import StepOption, ValidationResult
from app.natural_language import parse_quote_request
from app.recommender import (
    SELECTION_CONFIRMATION_THRESHOLD,
    QuoteRecommendation,
    RecommendationItem,
    _option_to_item,
    _profile_product_to_item,
    _score_confidence,
    render_recommendation_text,
)


def _item(confidence: float) -> RecommendationItem:
    return RecommendationItem(
        product_id="SYN-MAIN-1",
        short_description="Synthetic main model",
        quantity=1,
        step_id="fmt_step_1a",
        option_group="base",
        reason="Matched request keywords: compass.",
        source={},
        confidence=confidence,
        evidence=("Decision tree step: fmt_step_1a",),
    )


def _recommendation(
    item: RecommendationItem | None,
    status: str = "valid",
) -> QuoteRecommendation:
    return QuoteRecommendation(
        request=parse_quote_request("compass fmt system"),
        main_model=item,
        accessories=(),
        alternatives=(),
        validation=ValidationResult(status=status, issues=()),
        notices=(),
    )


def test_high_confidence_selection_needs_no_confirmation():
    recommendation = _recommendation(_item(0.9))

    assert recommendation.selection_confidence == 0.9
    assert recommendation.requires_confirmation is False


def test_low_confidence_selection_requires_confirmation():
    recommendation = _recommendation(_item(0.45))

    assert recommendation.requires_confirmation is True


def test_confirmation_threshold_is_inclusive_at_the_boundary():
    recommendation = _recommendation(_item(SELECTION_CONFIRMATION_THRESHOLD))

    assert recommendation.requires_confirmation is False


def test_blocking_rule_issue_always_requires_confirmation():
    recommendation = _recommendation(_item(1.0), status="invalid")

    assert recommendation.requires_confirmation is True


def test_missing_main_model_requires_confirmation():
    recommendation = _recommendation(None)

    assert recommendation.selection_confidence == 0.0
    assert recommendation.requires_confirmation is True


@pytest.mark.parametrize(
    ("score", "expected"),
    [(1_000, 1.0), (40, 0.9), (24, 0.75), (12, 0.6), (4, 0.45), (0, 0.25)],
)
def test_score_confidence_is_monotonic(score, expected):
    assert _score_confidence(score) == expected


def test_option_evidence_uses_decision_tree_vocabulary():
    option = StepOption(
        step_id="fmt_step_6",
        product_id="SYN-DET-1",
        option_group="detector",
        short_description="FOCUS detector",
        raw_constraint_text="Requires wireless access point",
        source={},
    )

    item = _option_to_item(option, "Matched request keywords: focus.")

    assert item.evidence == (
        "Decision tree step: fmt_step_6",
        "Option group: detector",
        "Rule text: Requires wireless access point",
    )


def test_profile_evidence_names_the_product_line_and_step():
    item = _profile_product_to_item(
        {
            "product_id": "SYN-MAIN-1",
            "product_line": "DRX-Compass FMT",
            "step_id": "Step 1a",
            "option_group": "base",
            "short_description": "Compass FMT base",
            "comment": "Default profile item",
        },
        "Started from the default DRX-Compass FMT quote profile.",
    )

    assert item.evidence == (
        "Decision tree product line: DRX-Compass FMT",
        "Decision tree step: Step 1a",
        "Option group: base",
        "Rule text: Default profile item",
    )


def test_rendered_text_states_confidence_and_decision_tree_basis():
    text = render_recommendation_text(_recommendation(_item(0.45)))

    assert "Selection confidence: 45%" in text
    assert "confirmation required" in text
    assert "Decision tree basis:" in text
    assert "Decision tree step: fmt_step_1a" in text
