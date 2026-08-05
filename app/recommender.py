from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.data_loader import QuotationSnapshot, load_snapshot
from app.models import Product, StepOption, ValidationResult
from app.natural_language import QuoteRequest, parse_quote_request
from app.requirement_fields import CONFIDENCE_CONFIRMATION_THRESHOLD
from app.rule_engine import QuotationRuleEngine


#: A selection at or above this confidence is applied without asking the user
#: to confirm it. Anything below is proposed and must be confirmed explicitly.
SELECTION_CONFIRMATION_THRESHOLD = CONFIDENCE_CONFIRMATION_THRESHOLD


@dataclass(frozen=True)
class RecommendationItem:
    product_id: str
    short_description: str
    quantity: int
    step_id: str | None
    option_group: str | None
    reason: str
    source: dict[str, object]
    #: How certain the deterministic selection is, in ``[0, 1]``.
    confidence: float = 1.0
    #: The decision-tree facts the选型 is based on, in the tree's own
    #: vocabulary (product line, step, option group, constraint text).
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuoteRecommendation:
    request: QuoteRequest
    main_model: RecommendationItem | None
    accessories: tuple[RecommendationItem, ...]
    alternatives: tuple[RecommendationItem, ...]
    validation: ValidationResult
    notices: tuple[str, ...]

    @property
    def selection_confidence(self) -> float:
        """Confidence of the proposed main model. Zero when there is none."""

        return self.main_model.confidence if self.main_model else 0.0

    @property
    def requires_confirmation(self) -> bool:
        """True when the user must confirm the选型 before it is applied.

        A confident, rule-clean proposal is applied directly; anything less —
        low confidence, no proposal, or a blocking rule issue — is only ever a
        suggestion.
        """

        if self.main_model is None:
            return True
        if self.validation.status == "invalid":
            return True
        return self.main_model.confidence < SELECTION_CONFIRMATION_THRESHOLD


DEFAULT_STEP_ORDER = (
    "step_1a",
    "step_2",
    "step_3",
    "step_5",
    "step_6",
    "step_8",
    "step_9a",
    "step_9b",
    "step_10",
    "step_11a",
)

STEP_LABELS = {
    "step_1a": "system console / PDU base",
    "step_2": "generator",
    "step_3": "tube stand",
    "step_5": "collimator",
    "step_6": "detector",
    "step_7": "wireless access point",
    "step_8": "X-ray tube",
    "step_9a": "wall stand",
    "step_9b": "wall stand bucky",
    "step_10": "grid",
    "step_11a": "table",
}

STEP_INTENT_KEYWORDS = {
    "step_1a": {"system", "console", "pdu", "base", "x-ray"},
    "step_2": {"generator", "kw", "phase"},
    "step_3": {"tube stand", "tubestand", "floor", "manual", "motorized"},
    "step_5": {"collimator"},
    "step_6": {"detector", "focus", "drx"},
    "step_7": {"wireless", "wifi", "wi-fi"},
    "step_8": {"tube", "khu"},
    "step_9a": {"wallstand", "wall", "stand"},
    "step_9b": {"bucky"},
    "step_10": {"grid"},
    "step_11a": {"table"},
}

SAMPLE_QUOTE_QUANTITY_OVERRIDES = {
    "6709828": 2,
    "6704506": 2,
    "6705859": 2,
    "8617060": 2,
}


class QuoteRecommender:
    def __init__(
        self,
        snapshot: QuotationSnapshot | None = None,
        profile_products: dict[str, tuple[dict[str, Any], ...]] | None = None,
    ) -> None:
        self.snapshot = snapshot or load_snapshot()
        self.engine = QuotationRuleEngine(self.snapshot)
        self.profile_products = (
            _load_decision_tree_profile_products()
            if profile_products is None
            else dict(profile_products)
        )
        self.profile_products_by_id = _group_profile_products_by_id(self.profile_products)

    def recommend_from_text(self, text: str, max_accessories: int | None = None) -> QuoteRecommendation:
        return self.recommend(parse_quote_request(text), max_accessories=max_accessories)

    def recommend(self, request: QuoteRequest, max_accessories: int | None = None) -> QuoteRecommendation:
        main_model = self._select_main_model(request)
        accessories = self._select_accessories(request, main_model, max_accessories)
        alternatives = self._select_alternatives(request, main_model, accessories)
        selected_product_ids = [
            item.product_id for item in (main_model, *accessories) if item is not None
        ]
        validation = self.engine.check_configuration(
            selected_product_ids,
            region=request.region,
        )
        return QuoteRecommendation(
            request=request,
            main_model=main_model,
            accessories=accessories,
            alternatives=alternatives,
            validation=validation,
            notices=self._build_notices(request, main_model, validation),
        )

    def _select_main_model(self, request: QuoteRequest) -> RecommendationItem | None:
        if request.product_ids:
            for product_id in request.product_ids:
                item = self._item_for_product_id(
                    product_id,
                    "You mentioned this product ID directly.",
                    confidence=1.0,
                )
                if item:
                    return item

        profile_line = self._profile_line_from_request(request)
        if profile_line:
            item = self._profile_main_model(profile_line, request)
            if item:
                return item

        if self._is_system_request(request):
            fallback = self._fallback_base_system(request)
            if fallback:
                return _option_to_item(
                    fallback,
                    "Started from the base system because the request asks for a complete X-ray system.",
                    confidence=0.6,
                )

        scored_options = sorted(
            (
                (self._score_option(option, request), option)
                for option in self.snapshot.step_options
                if self._option_allowed_by_request(option, request)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if scored_options and scored_options[0][0] > 0:
            score, option = scored_options[0]
            return _option_to_item(
                option,
                self._reason_for_score(option, request, score),
                confidence=self._match_confidence(scored_options),
            )

        fallback = self._fallback_base_system(request)
        if fallback:
            return _option_to_item(
                fallback,
                "I could not find a strong keyword match, so I started from the closest base system.",
                confidence=0.3,
            )
        return None

    @staticmethod
    def _match_confidence(
        scored_options: Sequence[tuple[int, StepOption]],
    ) -> float:
        """Confidence of the best match, reduced when a rival scores as well.

        An ambiguous request — two options with almost the same score — is a
        genuine choice for the user, so it is deliberately held below the
        confirmation threshold.
        """

        best_score = scored_options[0][0]
        confidence = _score_confidence(best_score)
        runner_up = scored_options[1][0] if len(scored_options) > 1 else 0
        if best_score < 1_000 and runner_up >= best_score:
            confidence = min(confidence, 0.5)
        return confidence

    def _select_accessories(
        self,
        request: QuoteRequest,
        main_model: RecommendationItem | None,
        max_accessories: int | None,
    ) -> tuple[RecommendationItem, ...]:
        family_prefix = self._selected_family_prefix(request, main_model)
        profile_line = self._profile_line_from_request(request) or self._profile_line_from_item(main_model)
        if profile_line:
            return self._select_profile_accessories(
                profile_line=profile_line,
                request=request,
                main_model=main_model,
                max_accessories=max_accessories,
            )

        if not family_prefix:
            return ()

        main_step_suffix = _step_suffix(main_model.step_id) if main_model else None
        selected_product_ids = {main_model.product_id} if main_model else set()
        requested_steps = self._requested_steps(request)
        step_order = tuple(
            dict.fromkeys(
                (*requested_steps, *DEFAULT_STEP_ORDER, *self._family_step_suffixes(family_prefix))
            )
        )
        accessories: list[RecommendationItem] = []

        for step_suffix in step_order:
            if max_accessories is not None and len(accessories) >= max_accessories:
                break
            if step_suffix == main_step_suffix:
                continue
            option = self._best_option_for_step(family_prefix, step_suffix, request)
            if not option or option.product_id in selected_product_ids:
                continue
            selected_product_ids.add(option.product_id)
            accessories.append(
                _option_to_item(
                    option,
                    f"Recommended as the {STEP_LABELS.get(step_suffix, step_suffix)} option for the same system family.",
                    confidence=_score_confidence(
                        self._score_option(option, request)
                    ),
                )
            )
        return tuple(accessories)

    def _family_step_suffixes(self, family_prefix: str) -> tuple[str, ...]:
        suffixes = {
            suffix
            for option in self.snapshot.step_options
            if option.step_id
            and option.step_id.startswith(f"{family_prefix}_")
            and (suffix := _step_suffix(option.step_id))
        }
        return tuple(sorted(suffixes, key=_step_sort_key))

    def _select_profile_accessories(
        self,
        profile_line: str,
        request: QuoteRequest,
        main_model: RecommendationItem | None,
        max_accessories: int | None,
    ) -> tuple[RecommendationItem, ...]:
        products = self.profile_products.get(profile_line, ())
        if not products:
            return ()

        main_step = main_model.step_id if main_model else None
        selected_product_ids = {main_model.product_id} if main_model else set()
        accessories: list[RecommendationItem] = []
        requested_steps = tuple(
            step
            for step in (_profile_step_from_suffix(suffix) for suffix in self._requested_steps(request))
            if step
        )
        step_order = tuple(dict.fromkeys((*requested_steps, *_profile_step_order(products))))

        for step_id in step_order:
            if max_accessories is not None and len(accessories) >= max_accessories:
                break
            if step_id == main_step:
                continue
            candidates = [
                product
                for product in products
                if product.get("step_id") == step_id
                and self._profile_product_allowed_by_region(product, request.region)
            ]
            if not candidates:
                continue
            product = max(candidates, key=lambda item: self._score_profile_product(item, request))
            product_id = str(product.get("product_id") or "").strip()
            if not product_id or product_id in selected_product_ids:
                continue
            selected_product_ids.add(product_id)
            accessories.append(
                _profile_product_to_item(
                    product,
                    f"Recommended from the default {profile_line} quote profile "
                    f"at {step_id}.",
                    confidence=_score_confidence(
                        self._score_profile_product(product, request)
                    )
                    if len(candidates) > 1
                    else 0.9,
                )
            )

        return tuple(accessories)

    def _profile_main_model(
        self, profile_line: str, request: QuoteRequest
    ) -> RecommendationItem | None:
        products = self.profile_products.get(profile_line, ())
        if not products:
            return None
        main_steps = {"Step 1", "Step 1a"}
        candidates = [
            product
            for product in products
            if product.get("step_id") in main_steps
            and self._profile_product_allowed_by_region(product, request.region)
        ]
        if not candidates:
            candidates = [
                product
                for product in products
                if product.get("step_id")
                and self._profile_product_allowed_by_region(product, request.region)
            ]
        if not candidates:
            return None
        product = max(candidates, key=lambda item: self._score_profile_product(item, request))
        return _profile_product_to_item(
            product,
            f"Started from the default {profile_line} quote profile.",
            confidence=(
                1.0
                if str(product.get("product_id") or "") in request.product_ids
                else 0.85
                if len(candidates) == 1
                else _score_confidence(
                    self._score_profile_product(product, request)
                )
            ),
        )

    def _score_profile_product(self, product: dict[str, Any], request: QuoteRequest) -> int:
        text = _profile_product_text(product)
        detail_text = _profile_product_detail_text(product)
        description_text = str(product.get("short_description") or "").casefold()
        score = 0
        product_id = str(product.get("product_id") or "")
        if product_id in request.product_ids:
            score += 1_000
        if request.acquisition_type and request.acquisition_type in text:
            score += 14
        for keyword in request.keywords:
            if _keyword_matches(keyword, text):
                score += 4
        if "focus" in request.keywords:
            score += 24 if "focus" in detail_text else 0
            score += 40 if "focus" in description_text else 0
        if "drx" in request.keywords:
            score += 24 if "drx" in detail_text else 0
            score += 40 if "drx" in description_text else 0
        if request.region and request.region in text:
            score += 8
        if request.region == "us" and "china" in text:
            score -= 20
        if request.region == "china" and "us" in text and "china" not in text:
            score -= 20
        return score

    def _profile_line_from_request(self, request: QuoteRequest) -> str | None:
        text = request.raw_text.casefold()
        if "rise" in text:
            return "DRX-Rise"
        if "revolution" in text:
            return "DRX-Revolution Plus"
        if "evolution" in text:
            return "DRX-Evolution Plus"
        if request.system_family == "OTC" or "otc" in text or "overhead" in text:
            return "DRX-Compass OTC"
        if request.system_family == "FMT" or "fmt" in text or "floor mount" in text:
            return "DRX-Compass FMT"
        if "compass" in text:
            return "DRX-Compass FMT"
        return None

    def _profile_product_allowed_by_region(
        self, product: dict[str, Any], region: str | None
    ) -> bool:
        product_id = str(product.get("product_id") or "").strip()
        if not product_id or not region:
            return True
        return self.engine.check_configuration([product_id], region=region).status != "invalid"

    def _profile_line_from_item(self, item: RecommendationItem | None) -> str | None:
        if item is None:
            return None
        profile_products = self.profile_products_by_id.get(item.product_id, ())
        if len(profile_products) == 1:
            return str(profile_products[0].get("product_line") or "") or None
        for product in profile_products:
            step_id = str(product.get("step_id") or "")
            if step_id == item.step_id:
                return str(product.get("product_line") or "") or None
        return None

    def _select_alternatives(
        self,
        request: QuoteRequest,
        main_model: RecommendationItem | None,
        accessories: tuple[RecommendationItem, ...],
        limit: int = 3,
    ) -> tuple[RecommendationItem, ...]:
        selected_product_ids = {item.product_id for item in accessories}
        if main_model:
            selected_product_ids.add(main_model.product_id)

        alternatives: list[RecommendationItem] = []
        scored_options = sorted(
            (
                (self._score_option(option, request), option)
                for option in self.snapshot.step_options
                if option.product_id not in selected_product_ids
                and self._option_allowed_by_request(option, request)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        for score, option in scored_options:
            if score <= 0 or len(alternatives) >= limit:
                break
            alternatives.append(
                _option_to_item(
                    option,
                    "Alternative match from the same request.",
                    confidence=_score_confidence(score),
                )
            )
        return tuple(alternatives)

    def _best_option_for_step(
        self, family_prefix: str, step_suffix: str, request: QuoteRequest
    ) -> StepOption | None:
        options = [
            option
            for option in self.snapshot.step_options
            if option.step_id == f"{family_prefix}_{step_suffix}"
            and not _looks_like_negative_option(option, request)
            and self._option_allowed_by_region(option, request.region)
        ]
        if not options:
            return None
        return max(options, key=lambda option: self._score_option(option, request))

    def _requested_steps(self, request: QuoteRequest) -> tuple[str, ...]:
        requested: list[str] = []
        keyword_set = set(request.keywords)
        for step_suffix, keywords in STEP_INTENT_KEYWORDS.items():
            if keyword_set.intersection(keywords):
                requested.append(step_suffix)
        return tuple(requested)

    def _score_option(self, option: StepOption, request: QuoteRequest) -> int:
        text = _option_text(option)
        score = 0
        step_suffix = _step_suffix(option.step_id)

        if option.product_id in request.product_ids:
            score += 1_000
        if request.system_family and option.step_id:
            score += 16 if option.step_id.startswith(request.system_family.casefold()) else -16
        if request.acquisition_type:
            if request.acquisition_type in text:
                score += 14
            if request.acquisition_type == "digital" and "analog" in text and "digital" not in text:
                score -= 6
            if request.acquisition_type == "analog" and "digital" in text and "analog" not in text:
                score -= 6

        for keyword in request.keywords:
            if _keyword_matches(keyword, text):
                score += 4
        if step_suffix in STEP_INTENT_KEYWORDS and set(request.keywords).intersection(
            STEP_INTENT_KEYWORDS[step_suffix]
        ):
            score += 12
        if step_suffix == "step_6":
            detector_description = option.short_description.casefold()
            if "focus" in request.keywords:
                score += 24 if "focus" in detector_description else -12
            if "drx" in request.keywords:
                score += 24 if "drx" in detector_description else -12
        if "low-cost" in request.keywords and any(term in text for term in ("manual", "40kw", "50kw")):
            score += 3
        return score

    def _reason_for_score(self, option: StepOption, request: QuoteRequest, score: int) -> str:
        matched_keywords = [
            keyword for keyword in request.keywords if _keyword_matches(keyword, _option_text(option))
        ]
        if matched_keywords:
            return "Matched request keywords: " + ", ".join(matched_keywords[:5]) + "."
        if request.system_family and option.step_id and option.step_id.startswith(request.system_family.casefold()):
            return f"Matched the requested {request.system_family} system family."
        return f"Best available catalog match with score {score}."

    def _fallback_base_system(self, request: QuoteRequest) -> StepOption | None:
        family_prefix = request.system_family.casefold() if request.system_family else "fmt"
        base_options = [
            option
            for option in self.snapshot.step_options
            if option.step_id == f"{family_prefix}_step_1a"
            and self._option_allowed_by_region(option, request.region)
        ]
        if request.acquisition_type:
            for option in base_options:
                if request.acquisition_type in _option_text(option):
                    return option
        return base_options[0] if base_options else None

    def _selected_family_prefix(
        self, request: QuoteRequest, main_model: RecommendationItem | None
    ) -> str | None:
        if request.system_family:
            return request.system_family.casefold()
        if main_model and main_model.step_id and "_" in main_model.step_id:
            return main_model.step_id.split("_", 1)[0]
        return None

    def _option_allowed_by_request(self, option: StepOption, request: QuoteRequest) -> bool:
        if request.system_family and option.step_id:
            if not option.step_id.startswith(request.system_family.casefold()):
                return False
        return self._option_allowed_by_region(option, request.region)

    def _option_allowed_by_region(self, option: StepOption, region: str | None) -> bool:
        if not region:
            return True
        result = self.engine.check_configuration([option.product_id], region=region)
        return result.status != "invalid"

    def _is_system_request(self, request: QuoteRequest) -> bool:
        keywords = set(request.keywords)
        return bool(request.system_family or keywords.intersection({"system", "x-ray"}))

    def _item_for_product_id(
        self,
        product_id: str,
        reason: str,
        *,
        confidence: float = 1.0,
    ) -> RecommendationItem | None:
        for option in self.snapshot.step_options:
            if option.product_id == product_id:
                return _option_to_item(option, reason, confidence=confidence)
        product = self.snapshot.products_by_id.get(product_id)
        if product:
            return _product_to_item(product, reason, confidence=confidence)
        profile_products = self.profile_products_by_id.get(product_id, ())
        if profile_products:
            return _profile_product_to_item(
                profile_products[0], reason, confidence=confidence
            )
        return None

    def _build_notices(
        self,
        request: QuoteRequest,
        main_model: RecommendationItem | None,
        validation: ValidationResult,
    ) -> tuple[str, ...]:
        notices: list[str] = []
        if not request.region:
            notices.append("Please confirm the sales region so region-only rules can be checked.")
        if main_model is None:
            notices.append("I could not map the request to a catalog product yet.")
        elif main_model.confidence < SELECTION_CONFIRMATION_THRESHOLD:
            notices.append(
                "The product选型 confidence is "
                f"{main_model.confidence:.0%}, which is below the "
                f"{SELECTION_CONFIRMATION_THRESHOLD:.0%} threshold, so please "
                "confirm the product before pricing."
            )
        if validation.status == "invalid":
            notices.append("The suggested set has blocking rule issues and should be adjusted before quotation.")
        return tuple(notices)


def render_recommendation_text(recommendation: QuoteRecommendation) -> str:
    if recommendation.main_model is None:
        return "I could not find a suitable model from the current catalog. Please add a clearer product type, system family, or product ID."

    main = recommendation.main_model
    lines = [
        f"I recommend {main.short_description} (product ID {main.product_id}) as the main model.",
        f"Reason: {main.reason}",
        f"Selection confidence: {main.confidence:.0%}"
        + (
            " (confirmation required)."
            if recommendation.requires_confirmation
            else " (applied automatically)."
        ),
    ]
    if main.evidence:
        lines.append("Decision tree basis:")
        lines.extend(f"- {line}" for line in main.evidence)

    if recommendation.accessories:
        lines.append("Recommended accessories/options:")
        for index, item in enumerate(recommendation.accessories, start=1):
            lines.append(
                f"{index}. {item.short_description} (product ID {item.product_id}) - {item.reason}"
            )

    if recommendation.validation.status == "valid":
        lines.append("Rule check: no blocking issue was found for the selected product IDs.")
    elif recommendation.validation.status == "incomplete":
        missing = ", ".join(recommendation.validation.missing_fields)
        lines.append(f"Rule check: more information is needed ({missing}).")
    else:
        lines.append("Rule check: blocking issue found.")

    for issue in recommendation.validation.issues[:4]:
        lines.append(f"- {issue.severity.upper()}: {issue.message}")

    if recommendation.notices:
        lines.append("Notes:")
        lines.extend(f"- {notice}" for notice in recommendation.notices)

    return "\n".join(lines)


def _option_to_item(
    option: StepOption,
    reason: str,
    *,
    confidence: float = 1.0,
    evidence: tuple[str, ...] = (),
) -> RecommendationItem:
    return RecommendationItem(
        product_id=option.product_id,
        short_description=option.short_description,
        quantity=_quantity_for_option(option),
        step_id=option.step_id,
        option_group=option.option_group,
        reason=reason,
        source=option.source,
        confidence=_clamp_confidence(confidence),
        evidence=evidence or _option_evidence(option),
    )


def _product_to_item(
    product: Product,
    reason: str,
    *,
    confidence: float = 1.0,
    evidence: tuple[str, ...] = (),
) -> RecommendationItem:
    return RecommendationItem(
        product_id=product.product_id,
        short_description=product.short_description,
        quantity=_quantity_for_product(product),
        step_id=None,
        option_group=None,
        reason=reason,
        source=product.source,
        confidence=_clamp_confidence(confidence),
        evidence=evidence,
    )


def _profile_product_to_item(
    product: dict[str, Any],
    reason: str,
    *,
    confidence: float = 1.0,
    evidence: tuple[str, ...] = (),
) -> RecommendationItem:
    product_id = str(product.get("product_id") or "").strip()
    description = str(product.get("short_description") or "").strip()
    return RecommendationItem(
        product_id=product_id,
        short_description=description,
        quantity=_quantity_for_item(product_id, description),
        step_id=str(product.get("step_id") or "").strip() or None,
        option_group=str(product.get("option_group") or "").strip() or None,
        reason=reason,
        source=dict(product.get("source") or {}),
        confidence=_clamp_confidence(confidence),
        evidence=evidence or _profile_evidence(product),
    )


def _clamp_confidence(value: float) -> float:
    return round(min(1.0, max(0.0, float(value))), 2)


def _option_evidence(option: StepOption) -> tuple[str, ...]:
    """Decision-tree facts behind a catalog step option."""

    lines: list[str] = []
    if option.step_id:
        lines.append(f"Decision tree step: {option.step_id}")
    if option.option_group:
        lines.append(f"Option group: {option.option_group}")
    if option.raw_constraint_text:
        lines.append(f"Rule text: {option.raw_constraint_text}")
    return tuple(lines)


def _profile_evidence(product: dict[str, Any]) -> tuple[str, ...]:
    """Decision-tree facts behind a quote-profile product."""

    lines: list[str] = []
    product_line = str(product.get("product_line") or "").strip()
    step_id = str(product.get("step_id") or "").strip()
    option_group = str(product.get("option_group") or "").strip()
    comment = str(product.get("comment") or "").strip()
    if product_line:
        lines.append(f"Decision tree product line: {product_line}")
    if step_id:
        lines.append(f"Decision tree step: {step_id}")
    if option_group:
        lines.append(f"Option group: {option_group}")
    if comment:
        lines.append(f"Rule text: {comment}")
    return tuple(lines)


def _score_confidence(score: int, *, exact_product_id: bool = False) -> float:
    """Map a deterministic match score onto a calibrated confidence.

    An explicit product ID is certain. Otherwise the confidence rises with the
    strength of the keyword, family and step evidence, and only a strong match
    reaches :data:`SELECTION_CONFIRMATION_THRESHOLD`, so a weak match is always
    proposed for confirmation rather than applied silently.
    """

    if exact_product_id or score >= 1_000:
        return 1.0
    if score >= 40:
        return 0.9
    if score >= 24:
        return 0.75
    if score >= 12:
        return 0.6
    if score > 0:
        return 0.45
    return 0.25


def _option_text(option: StepOption) -> str:
    return " ".join(
        part
        for part in (
            option.product_id,
            option.step_id or "",
            option.option_group or "",
            option.short_description,
            option.raw_constraint_text or "",
        )
        if part
    ).casefold()


def _profile_product_text(product: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in (
            product.get("product_id"),
            product.get("product_line"),
            product.get("step_id"),
            product.get("option_group"),
            product.get("short_description"),
            product.get("comment"),
        )
        if part
    ).casefold()


def _profile_product_detail_text(product: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in (
            product.get("product_id"),
            product.get("step_id"),
            product.get("option_group"),
            product.get("short_description"),
            product.get("comment"),
        )
        if part
    ).casefold()


def _quantity_for_option(option: StepOption) -> int:
    return _quantity_for_item(option.product_id, option.short_description)


def _quantity_for_product(product: Product) -> int:
    return _quantity_for_item(product.product_id, product.short_description)


def _quantity_for_item(product_id: str, description: str) -> int:
    override = SAMPLE_QUOTE_QUANTITY_OVERRIDES.get(product_id)
    if override is not None:
        return override

    match = re.search(r"\b(?:quantity|qty)\s*(?:of|:)?\s*(\d+)\b", description, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return 1


def _keyword_matches(keyword: str, text: str) -> bool:
    normalized_keyword = keyword.casefold().strip()
    if not normalized_keyword:
        return False
    if re.fullmatch(r"[a-z0-9+/-]+", normalized_keyword):
        pattern = r"(?<![a-z0-9])" + re.escape(normalized_keyword) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return normalized_keyword in text


def _step_suffix(step_id: str | None) -> str | None:
    if not step_id or "_" not in step_id:
        return None
    return step_id.split("_", 1)[1]


def _step_sort_key(step_suffix: str) -> tuple[int, str]:
    match = re.fullmatch(r"step_(\d+)([a-z]*)", step_suffix)
    if not match:
        return (999, step_suffix)
    return (int(match.group(1)), match.group(2))


def _profile_step_sort_key(step_id: str) -> tuple[int, str]:
    match = re.fullmatch(r"step\s*(\d+)([a-z]*)", step_id.casefold().strip())
    if not match:
        return (999, step_id)
    return (int(match.group(1)), match.group(2))


def _profile_step_order(products: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    step_ids = {str(product.get("step_id") or "").strip() for product in products}
    step_ids.discard("")
    return tuple(sorted(step_ids, key=_profile_step_sort_key))


def _profile_step_from_suffix(step_suffix: str | None) -> str | None:
    if not step_suffix:
        return None
    match = re.fullmatch(r"step_(\d+)([a-z]*)", step_suffix.casefold().strip())
    if not match:
        return None
    return f"Step {match.group(1)}{match.group(2)}"


def _load_decision_tree_profile_products() -> dict[str, tuple[dict[str, Any], ...]]:
    path = Path(__file__).resolve().parents[1] / "rules" / "decision_tree_normalized_rules.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for product in data.get("products", []):
        product_line = str(product.get("product_line") or "").strip()
        if not product_line or product_line == "DRX-Compass OTC/FMT":
            continue
        grouped.setdefault(product_line, []).append(product)
    return {product_line: tuple(products) for product_line, products in grouped.items()}


def _group_profile_products_by_id(
    profile_products: dict[str, tuple[dict[str, Any], ...]]
) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for products in profile_products.values():
        for product in products:
            product_id = str(product.get("product_id") or "").strip()
            if product_id:
                grouped.setdefault(product_id, []).append(product)
    return {product_id: tuple(products) for product_id, products in grouped.items()}


def _looks_like_negative_option(option: StepOption, request: QuoteRequest) -> bool:
    text = _option_text(option)
    keywords = set(request.keywords)
    if "no table" in text and "table" not in keywords:
        return True
    if ("no ws" in text or "no wall" in text) and "wallstand" not in keywords:
        return True
    return False
