from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping

from app.config import (
    DEMO_MIN_GROSS_MARGIN_PERCENT,
    DEMO_QUANTITY_DISCOUNT_POLICY,
    SAP_BASE_CURRENCY,
)
from app.pricing_data import PricingRecord, load_pricing_records, normalize_decimal
from app.quotation_models import ComparableQuotation, PricingResult, QuotationDraft


MONEY_QUANTUM = Decimal("0.01")
PERCENT_QUANTUM = Decimal("0.01")
CATALOG_PRICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "rules"
    / "decision_tree_normalized_rules.json"
)
DESCRIPTION_STOP_WORDS = frozenset(
    {
        "a",
        "and",
        "for",
        "of",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True)
class MatchedPricingRecord:
    record: PricingRecord
    tier: int
    match_score: Decimal
    match_reasons: tuple[str, ...]


class PricingEngine:
    def __init__(
        self,
        records: Iterable[PricingRecord] | None = None,
        catalog_list_prices: Mapping[str, Decimal] | None = None,
    ) -> None:
        self.records = (
            tuple(records) if records is not None else load_pricing_records()
        )
        self.catalog_list_prices = (
            dict(catalog_list_prices)
            if catalog_list_prices is not None
            else dict(load_catalog_list_prices())
        )

    def analyse(
        self,
        draft: QuotationDraft,
        *,
        product_description: str = "",
        product_family: str = "",
    ) -> PricingResult:
        warnings: list[str] = []
        assumptions = [
            (
                f"Archived pricing values are treated as {SAP_BASE_CURRENCY}; "
                "the workbook has no confirmed currency column."
            ),
            "Estimated cost uses the centralized additive demo cost policy.",
            "Configured demo pricing floors are applied when supporting data is available.",
        ]
        if draft.currency.upper() != SAP_BASE_CURRENCY:
            warnings.append(
                f"No currency conversion is configured. Results remain in {SAP_BASE_CURRENCY}, "
                f"not the requested {draft.currency.upper()}."
            )
        if not draft.selected_product_ids:
            return _unavailable_result(
                (),
                assumptions,
                warnings + ["No product has been selected for pricing."],
            )
        if len(draft.selected_product_ids) > 1:
            warnings.append(
                "Only the first selected product is priced in this Phase 3 demo."
            )

        product_id = draft.selected_product_ids[0]
        comparables = self.find_comparables(
            product_id,
            product_description=product_description,
            product_family=product_family,
        )
        if not comparables:
            fallback_price = self.catalog_list_prices.get(
                normalize_catalog_identifier(product_id)
            )
            if not _is_valid_price(fallback_price):
                return _unavailable_result(
                    draft.selected_product_ids,
                    assumptions,
                    warnings
                    + ["No matching archived pricing records or catalog list price were found."],
                )
        strong = tuple(match for match in comparables if match.tier >= 2)
        price_pool = strong or comparables
        valid_net_prices = [
            match.record.net_price
            for match in strong
            if _is_valid_price(match.record.net_price)
        ]
        valid_list_prices = [
            match.record.list_price
            for match in price_pool
            if _is_valid_price(match.record.list_price)
        ]
        reference_net = _median_decimal(valid_net_prices)
        reference_list = _median_decimal(valid_list_prices)

        if reference_net is not None:
            base_price = reference_net
            assumptions.append(
                "The starting unit price is the median valid net price from strong comparables."
            )
        elif reference_list is not None:
            base_price = reference_list
            assumptions.append(
                "No usable strong net prices were available; median comparable list price was used."
            )
        else:
            base_price = self.catalog_list_prices.get(
                normalize_catalog_identifier(product_id)
            )
            if _is_valid_price(base_price):
                assumptions.append(
                    "Comparable prices were unavailable; normalized catalog list price was used."
                )
            else:
                return _unavailable_result(
                    draft.selected_product_ids,
                    assumptions,
                    warnings + ["Matching records contain no usable prices."],
                    comparables=comparables,
                )

        quantity_discount = demo_quantity_discount_percent(draft.quantity)
        assumptions.append(
            f"The Phase 3 demo quantity adjustment is {quantity_discount}%."
        )
        recommended_price = base_price * (
            Decimal("1") - quantity_discount / Decimal("100")
        )

        minimum_prices = [
            match.record.minimum_price
            for match in strong
            if _is_valid_price(match.record.minimum_price)
        ]
        minimum_floor = max(minimum_prices) if minimum_prices else None
        estimated_cost = _median_decimal(
            cost
            for cost in (
                calculate_demo_base_cost(match.record) for match in strong
            )
            if cost is not None
        )
        margin_floor = (
            _gross_margin_floor(estimated_cost)
            if estimated_cost is not None
            else None
        )

        applied_floors: list[Decimal] = []
        if minimum_floor is not None:
            applied_floors.append(minimum_floor)
        if margin_floor is not None:
            applied_floors.append(margin_floor)
        if applied_floors and recommended_price < max(applied_floors):
            recommended_price = max(applied_floors)
            warnings.append(
                "A configured demo pricing floor increased the recommendation."
            )

        recommended_price = _round_money(recommended_price)
        quantity = max(draft.quantity, 1)
        total_price = _round_money(recommended_price * quantity)
        reference_list = _round_optional_money(reference_list)
        reference_net = _round_optional_money(reference_net)
        estimated_cost = _round_optional_money(estimated_cost)
        minimum_floor = _round_optional_money(minimum_floor)
        margin_floor = _round_optional_money(margin_floor)

        gross_margin_amount: Decimal | None = None
        gross_margin_percent: Decimal | None = None
        if estimated_cost is not None and recommended_price > 0:
            gross_margin_amount = _round_money(
                (recommended_price - estimated_cost) * quantity
            )
            gross_margin_percent = _round_percent(
                (recommended_price - estimated_cost)
                / recommended_price
                * Decimal("100")
            )
        else:
            warnings.append(
                "Estimated cost and gross margin are unavailable because COGS is missing."
            )

        discount_percent = (
            _round_percent(
                (reference_list - recommended_price)
                / reference_list
                * Decimal("100")
            )
            if reference_list is not None and reference_list > 0
            else None
        )
        confidence_score, confidence_label = _confidence(comparables)
        evidence = [_to_comparable(match, draft.quantity) for match in comparables]

        return PricingResult(
            selected_product_ids=list(draft.selected_product_ids),
            currency=SAP_BASE_CURRENCY,
            recommended_unit_price=_to_float(recommended_price),
            total_price=_to_float(total_price),
            reference_list_price=_to_optional_float(reference_list),
            reference_net_price=_to_optional_float(reference_net),
            comparable_median_price=_to_optional_float(
                reference_net or reference_list
            ),
            minimum_price_floor=_to_optional_float(minimum_floor),
            gross_margin_floor=_to_optional_float(margin_floor),
            estimated_cost=_to_optional_float(estimated_cost),
            gross_margin_amount=_to_optional_float(gross_margin_amount),
            gross_margin_percent=_to_optional_float(gross_margin_percent),
            discount_percent=_to_optional_float(discount_percent),
            comparable_count=len(comparables),
            cost_basis_complete=_cost_basis_complete(strong),
            confidence_score=confidence_score,
            confidence_label=confidence_label,
            assumptions=assumptions,
            warnings=warnings,
            internal_evidence=evidence,
        )

    def find_comparables(
        self,
        selected_product_id: str,
        *,
        product_description: str = "",
        product_family: str = "",
        limit: int = 5,
    ) -> tuple[MatchedPricingRecord, ...]:
        selected_id = selected_product_id.strip()
        normalized_id = normalize_catalog_identifier(selected_id)
        normalized_description = normalize_description(product_description)
        description_tokens = _description_tokens(product_description)
        normalized_family = normalize_description(product_family)
        if not normalized_family:
            normalized_family = _infer_family(product_description)

        matches: list[MatchedPricingRecord] = []
        for record in self.records:
            record_id = record.product_id.strip()
            record_normalized_id = normalize_catalog_identifier(record_id)
            record_description = normalize_description(record.description)
            record_tokens = _description_tokens(record.description)
            record_family = normalize_description(record.product_family)
            reasons: list[str] = []
            tier = 0
            score = Decimal("0")

            if selected_id and record_id.casefold() == selected_id.casefold():
                tier = 4
                score += Decimal("100")
                reasons.append("exact product ID")
            elif normalized_id and record_normalized_id == normalized_id:
                tier = 3
                score += Decimal("90")
                reasons.append("exact normalized catalog ID")

            if (
                normalized_description
                and record_description == normalized_description
            ):
                tier = max(tier, 2)
                score += Decimal("60")
                reasons.append("exact normalized description")

            overlap = _keyword_overlap(description_tokens, record_tokens)
            if overlap >= Decimal("0.60"):
                tier = max(tier, 2)
                score += overlap * Decimal("40")
                reasons.append("strong description keyword overlap")
            elif overlap >= Decimal("0.35"):
                score += overlap * Decimal("30")
                reasons.append("description keyword overlap")

            same_family = bool(
                normalized_family
                and record_family
                and (
                    normalized_family == record_family
                    or normalized_family in record_family
                    or record_family in normalized_family
                )
            )
            if same_family:
                tier = max(tier, 1)
                score += Decimal("20")
                reasons.append("same product family")

            if tier:
                matches.append(
                    MatchedPricingRecord(
                        record=record,
                        tier=tier,
                        match_score=score,
                        match_reasons=tuple(reasons),
                    )
                )

        matches.sort(
            key=lambda match: (
                -match.tier,
                -match.match_score,
                match.record.source_id,
            )
        )
        return tuple(matches[: max(1, limit)])


def calculate_demo_base_cost(record: PricingRecord) -> Decimal | None:
    if record.cogs is None:
        return None
    additive_values = (
        record.installation_cogs,
        record.warranty_cogs,
        record.freight,
        record.duty,
        record.tariff,
    )
    return record.cogs + sum(
        (value or Decimal("0") for value in additive_values),
        Decimal("0"),
    )


def _cost_basis_complete(
    matches: tuple[MatchedPricingRecord, ...],
) -> bool:
    if not matches:
        return False
    fields = (
        "cogs",
        "installation_cogs",
        "warranty_cogs",
        "freight",
        "duty",
        "tariff",
    )
    return all(
        all(getattr(match.record, field_name) is not None for field_name in fields)
        for match in matches
    )


def demo_quantity_discount_percent(quantity: int) -> Decimal:
    normalized_quantity = max(quantity, 1)
    for minimum, maximum, discount in DEMO_QUANTITY_DISCOUNT_POLICY:
        if normalized_quantity >= minimum and (
            maximum is None or normalized_quantity <= maximum
        ):
            return Decimal(str(discount))
    return Decimal("0")


def normalize_catalog_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def normalize_description(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


@lru_cache(maxsize=1)
def load_catalog_list_prices() -> Mapping[str, Decimal]:
    if not CATALOG_PRICE_PATH.exists():
        return {}
    try:
        payload = json.loads(CATALOG_PRICE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    prices: dict[str, Decimal] = {}
    for product in payload.get("products", []):
        product_id = normalize_catalog_identifier(
            str(product.get("product_id") or "")
        )
        list_price = normalize_decimal(product.get("list_price"))
        if product_id and _is_valid_price(list_price):
            prices.setdefault(product_id, list_price)
    return prices


def _description_tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in DESCRIPTION_STOP_WORDS
    )


def _keyword_overlap(
    expected: frozenset[str],
    actual: frozenset[str],
) -> Decimal:
    if not expected or not actual:
        return Decimal("0")
    return Decimal(len(expected.intersection(actual))) / Decimal(len(expected))


def _infer_family(description: str) -> str:
    normalized = normalize_description(description)
    for family in ("drx compass", "drx rise", "drx revolution", "drx evolution"):
        if family in normalized:
            return family
    if "compass" in normalized:
        return "compass"
    return ""


def _gross_margin_floor(estimated_cost: Decimal) -> Decimal:
    threshold = Decimal(str(DEMO_MIN_GROSS_MARGIN_PERCENT)) / Decimal("100")
    return estimated_cost / (Decimal("1") - threshold)


def _confidence(
    comparables: tuple[MatchedPricingRecord, ...],
) -> tuple[float, str]:
    valid_prices = sum(
        1
        for match in comparables
        if _is_valid_price(match.record.net_price)
        or _is_valid_price(match.record.list_price)
    )
    has_exact = any(match.tier >= 3 for match in comparables)
    has_strong = any(match.tier >= 2 for match in comparables)
    if has_exact and valid_prices >= 3:
        return 0.9, "High"
    if (has_exact or has_strong) and 1 <= valid_prices <= 2:
        return 0.65, "Medium"
    return 0.35, "Low"


def _to_comparable(
    match: MatchedPricingRecord,
    quantity: int,
) -> ComparableQuotation:
    record = match.record
    return ComparableQuotation(
        source_id=record.source_id,
        source_sheet=record.source_sheet,
        product_id=record.product_id,
        description=record.description,
        quantity=quantity,
        list_price=_to_optional_float(_round_optional_money(record.list_price)),
        net_price=_to_optional_float(_round_optional_money(record.net_price)),
        minimum_price=_to_optional_float(
            _round_optional_money(record.minimum_price)
        ),
        cost=_to_optional_float(
            _round_optional_money(calculate_demo_base_cost(record))
        ),
        currency=record.currency,
        match_score=float(_round_percent(match.match_score)),
        match_reasons=list(match.match_reasons),
    )


def _unavailable_result(
    selected_product_ids: Iterable[str],
    assumptions: list[str],
    warnings: list[str],
    *,
    comparables: tuple[MatchedPricingRecord, ...] = (),
) -> PricingResult:
    return PricingResult(
        selected_product_ids=list(selected_product_ids),
        currency=SAP_BASE_CURRENCY,
        comparable_count=len(comparables),
        confidence_score=0.0,
        confidence_label="Low",
        assumptions=assumptions,
        warnings=warnings,
        internal_evidence=[_to_comparable(match, 1) for match in comparables],
    )


def _median_decimal(values: Iterable[Decimal]) -> Decimal | None:
    usable = [value for value in values if _is_valid_price(value)]
    return median(usable) if usable else None


def _is_valid_price(value: Decimal | None) -> bool:
    return value is not None and value > 0


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _round_optional_money(value: Decimal | None) -> Decimal | None:
    return _round_money(value) if value is not None else None


def _round_percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def _to_float(value: Decimal) -> float:
    return float(value)


def _to_optional_float(value: Decimal | None) -> float | None:
    return _to_float(value) if value is not None else None
