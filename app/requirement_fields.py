"""Deterministic specification of every requirement field.

Agent 1 and the structured form both produce *candidate* values. A candidate
may never be written to the quotation domain model directly: it is first
checked here against the field type and, where one exists, the allowed value
set. Anything that fails is rejected with a reason, so an invalid or
hallucinated AI extraction cannot corrupt a quotation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class RequirementValidationError(ValueError):
    """Raised when a candidate value cannot be coerced into a field value."""


ALLOWED_CURRENCIES = ("USD", "EUR", "CNY", "SGD", "JPY", "GBP", "AUD", "HKD")
ALLOWED_INCOTERMS = ("EXW", "FCA", "FOB", "CIF", "CIP", "DAP", "DDP")
CURRENCY_ALIASES = {"RMB": "CNY", "US$": "USD", "USD$": "USD", "€": "EUR"}

#: Candidate values at or above this confidence are merged silently. Anything
#: below is held back and must be confirmed explicitly by the user.
CONFIDENCE_CONFIRMATION_THRESHOLD = 0.7


def _clean_text(value: Any, *, max_length: int = 200) -> str:
    text = str(value).strip()
    if not text:
        raise RequirementValidationError("value cannot be blank")
    if len(text) > max_length:
        raise RequirementValidationError(
            f"value is longer than {max_length} characters"
        )
    return text


def _long_text(value: Any) -> str:
    return _clean_text(value, max_length=2000)


def _region(value: Any) -> str:
    return _clean_text(value, max_length=100).casefold()


def _quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise RequirementValidationError("quantity must be a whole number")
    try:
        quantity = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise RequirementValidationError(
            "quantity must be a whole number"
        ) from error
    if quantity <= 0:
        raise RequirementValidationError("quantity must be greater than zero")
    if quantity > 999:
        raise RequirementValidationError("quantity must be 999 or fewer units")
    return quantity


def _money(value: Any) -> float:
    try:
        amount = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError) as error:
        raise RequirementValidationError("value must be a number") from error
    if amount <= 0:
        raise RequirementValidationError("value must be greater than zero")
    return amount


def _currency(value: Any) -> str:
    text = _clean_text(value, max_length=8).upper()
    text = CURRENCY_ALIASES.get(text, text)
    if text not in ALLOWED_CURRENCIES:
        allowed = ", ".join(ALLOWED_CURRENCIES)
        raise RequirementValidationError(f"currency must be one of: {allowed}")
    return text


def _incoterm(value: Any) -> str:
    text = _clean_text(value, max_length=8).upper()
    if text not in ALLOWED_INCOTERMS:
        allowed = ", ".join(ALLOWED_INCOTERMS)
        raise RequirementValidationError(f"Incoterm must be one of: {allowed}")
    return text


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = [part for part in _split_list_text(value)]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        raise RequirementValidationError("value must be a list of texts")
    cleaned: list[str] = []
    for part in parts:
        text = part.strip(" \t,;")
        if not text:
            continue
        if len(text) > 200:
            raise RequirementValidationError(
                "list entries must be 200 characters or fewer"
            )
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise RequirementValidationError("value cannot be blank")
    if len(cleaned) > 20:
        raise RequirementValidationError("at most 20 entries are supported")
    return cleaned


def _split_list_text(value: str) -> list[str]:
    normalized = value.replace("、", ",").replace("；", ",").replace(";", ",")
    normalized = normalized.replace("，", ",").replace("\n", ",")
    return normalized.split(",")


@dataclass(frozen=True)
class RequirementFieldSpec:
    """Type, question and validator for one requirement field."""

    name: str
    label: str
    question: str
    validator: Callable[[Any], Any]
    allowed_values: tuple[str, ...] = ()
    is_list: bool = False

    def validate(self, value: Any) -> Any:
        if value is None:
            raise RequirementValidationError("value cannot be empty")
        return self.validator(value)


REQUIREMENT_FIELD_SPECS: dict[str, RequirementFieldSpec] = {
    spec.name: spec
    for spec in (
        RequirementFieldSpec(
            name="customer_name",
            label="customer name",
            question="What is the customer name?",
            validator=_clean_text,
        ),
        RequirementFieldSpec(
            name="region",
            label="region",
            question="Which sales region is this quotation for?",
            validator=_region,
        ),
        RequirementFieldSpec(
            name="product_query",
            label="product request",
            question="What product or system should I configure?",
            validator=_long_text,
        ),
        RequirementFieldSpec(
            name="quantity",
            label="quantity",
            question="What quantity is required?",
            validator=_quantity,
        ),
        RequirementFieldSpec(
            name="currency",
            label="currency",
            question="Which currency should the quotation use?",
            validator=_currency,
            allowed_values=ALLOWED_CURRENCIES,
        ),
        RequirementFieldSpec(
            name="incoterm",
            label="Incoterm",
            question=(
                "Which Incoterm applies "
                "(EXW, FCA, FOB, CIF, CIP, DAP, or DDP)?"
            ),
            validator=_incoterm,
            allowed_values=ALLOWED_INCOTERMS,
        ),
        RequirementFieldSpec(
            name="delivery_location",
            label="delivery location",
            question="What is the delivery location?",
            validator=_clean_text,
        ),
        RequirementFieldSpec(
            name="intended_use",
            label="intended use",
            question="What is the intended clinical or operational use?",
            validator=_long_text,
        ),
        RequirementFieldSpec(
            name="budget_notes",
            label="budget notes",
            question="Are there any budget constraints to record?",
            validator=_long_text,
        ),
        RequirementFieldSpec(
            name="target_price",
            label="target price",
            question="Is there a target price?",
            validator=_money,
        ),
        RequirementFieldSpec(
            name="requested_accessories",
            label="requested accessories",
            question="Which accessories has the customer asked for?",
            validator=_string_list,
            is_list=True,
        ),
        RequirementFieldSpec(
            name="requested_services",
            label="requested services",
            question="Which services has the customer asked for?",
            validator=_string_list,
            is_list=True,
        ),
        RequirementFieldSpec(
            name="constraints",
            label="constraints",
            question="Are there any other constraints I should record?",
            validator=_string_list,
            is_list=True,
        ),
        RequirementFieldSpec(
            name="selected_product_ids",
            label="selected products",
            question="Which product should I quote?",
            validator=_string_list,
            is_list=True,
        ),
    )
}

#: Fields Agent 1 is allowed to propose. Prices, margins, approval status and
#: rule outcomes are deliberately absent: AI may never create commercial state.
AGENT_EXTRACTABLE_FIELDS: tuple[str, ...] = (
    "customer_name",
    "region",
    "product_query",
    "quantity",
    "currency",
    "incoterm",
    "delivery_location",
    "intended_use",
    "budget_notes",
    "requested_accessories",
    "requested_services",
    "constraints",
)


def field_spec(field_name: str) -> RequirementFieldSpec:
    spec = REQUIREMENT_FIELD_SPECS.get(field_name)
    if spec is None:
        raise RequirementValidationError(f"Unknown requirement field: {field_name}")
    return spec


def validate_field(field_name: str, value: Any) -> Any:
    """Validate ``value`` for ``field_name`` and return the normalised value."""

    return field_spec(field_name).validate(value)


def field_question(field_name: str) -> str:
    return field_spec(field_name).question


def field_label(field_name: str) -> str:
    return field_spec(field_name).label
