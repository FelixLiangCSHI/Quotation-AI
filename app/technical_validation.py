from __future__ import annotations

import re
from typing import Any

from app.quotation_models import QuotationDraft, TechnicalValidationResult
from app.recommender import QuoteRecommendation, RecommendationItem
from app.rule_engine import QuotationRuleEngine


TECHNICAL_CHECKS = {
    "products": "Product and regional restrictions",
    "system": "System component compatibility",
    "detector_grid": "Detector and grid compatibility",
    "generator_tube": "Generator and tube compatibility",
}


def validate_technical_configuration(
    draft: QuotationDraft,
    recommendation: QuoteRecommendation | None,
    engine: QuotationRuleEngine,
) -> TechnicalValidationResult:
    items = _selected_configuration_items(draft, recommendation)
    product_ids = list(
        dict.fromkeys(
            [*draft.selected_product_ids, *(item.product_id for item in items)]
        )
    )
    descriptions = " ".join(item.short_description for item in items)
    request = recommendation.request if recommendation else None

    system_family = (
        request.system_family if request else None
    ) or _infer_system_family(descriptions)
    acquisition_type = (
        request.acquisition_type if request else None
    ) or _infer_acquisition_type(descriptions)
    tube_stand_id = _product_id_for_step(items, "3")
    wallstand_id = _product_id_for_step(items, "9a")
    table_id = _product_id_for_step(items, "11a")
    grid_id = _product_id_for_step(items, "10")
    detector_type = _infer_detector_type(descriptions)
    grid_position = _infer_grid_position(
        draft.product_query,
        descriptions,
    )
    generator = _infer_generator(descriptions, engine)
    tube_spec = _infer_tube_spec(descriptions, generator, engine)

    evaluated_inputs = {
        "product_ids": product_ids,
        "region": draft.region or None,
        "system_family": system_family,
        "acquisition_type": acquisition_type,
        "tube_stand_id": tube_stand_id,
        "wallstand_id": wallstand_id,
        "table_id": table_id,
        "grid_id": grid_id,
        "grid_position": grid_position,
        "detector_type": detector_type,
        "generator": generator,
        "tube_spec": tube_spec,
    }
    raw_result = engine.check_configuration(
        product_ids,
        region=draft.region or None,
        system_family=system_family,
        acquisition_type=acquisition_type,
        tube_stand_id=tube_stand_id,
        wallstand_id=wallstand_id,
        table_id=table_id,
        grid_id=grid_id,
        grid_position=grid_position,
        detector_type=detector_type,
        generator=generator,
        tube_spec=tube_spec,
    )

    errors = [
        issue.message for issue in raw_result.issues if issue.severity == "error"
    ]
    warnings = [
        issue.message for issue in raw_result.issues if issue.severity == "warning"
    ]
    passed_checks = [
        issue.message for issue in raw_result.issues if issue.severity == "info"
    ]
    not_evaluated: list[str] = []

    issue_codes = {issue.code for issue in raw_result.issues}
    missing = set(raw_result.missing_fields)
    if product_ids and draft.region:
        if not issue_codes.intersection({"unknown_product", "region_not_allowed"}):
            passed_checks.append(TECHNICAL_CHECKS["products"])
    else:
        not_evaluated.append(
            f"{TECHNICAL_CHECKS['products']}: product selection or region unavailable"
        )

    system_fields = {
        "system_family",
        "acquisition_type",
        "tube_stand_id",
        "wallstand_id",
        "table_id",
    }
    system_inputs_available = bool(
        system_family
        and acquisition_type
        and (tube_stand_id or wallstand_id or table_id)
        and not system_fields.intersection(missing)
    )
    if system_inputs_available:
        if not issue_codes.intersection(
            {
                "compatibility_not_found",
                "system_not_supported",
                "system_conditionally_supported",
            }
        ):
            passed_checks.append(TECHNICAL_CHECKS["system"])
    else:
        not_evaluated.append(
            f"{TECHNICAL_CHECKS['system']}: complete system-component set unavailable"
        )

    if grid_id and (grid_position or detector_type):
        if not issue_codes.intersection(
            {
                "unknown_grid",
                "grid_position_not_supported",
                "detector_grid_not_supported",
            }
        ):
            passed_checks.append(TECHNICAL_CHECKS["detector_grid"])
    else:
        not_evaluated.append(
            f"{TECHNICAL_CHECKS['detector_grid']}: applicable grid details unavailable"
        )

    if generator:
        if not issue_codes.intersection(
            {
                "unknown_generator",
                "generator_tube_spec_not_found",
            }
        ):
            passed_checks.append(TECHNICAL_CHECKS["generator_tube"])
    else:
        not_evaluated.append(
            f"{TECHNICAL_CHECKS['generator_tube']}: applicable generator details unavailable"
        )

    for field_name in sorted(missing):
        detail = f"Rule input not evaluated: {field_name.replace('_', ' ')} unavailable"
        if detail not in not_evaluated:
            not_evaluated.append(detail)

    if errors:
        status = "invalid"
    elif warnings:
        status = "valid_with_warnings"
    elif not_evaluated:
        status = "not_fully_evaluated"
    else:
        status = "valid"

    return TechnicalValidationResult(
        status=status,
        errors=errors,
        warnings=warnings,
        passed_checks=list(dict.fromkeys(passed_checks)),
        not_evaluated_checks=list(dict.fromkeys(not_evaluated)),
        evaluated_inputs={
            key: value for key, value in evaluated_inputs.items() if value
        },
        checked_rules=list(
            dict.fromkeys(
                [
                    *(issue.code for issue in raw_result.issues),
                    *(
                        f"TV-{index:03d}"
                        for index, check in enumerate(
                            TECHNICAL_CHECKS,
                            start=1,
                        )
                        if TECHNICAL_CHECKS[check] in passed_checks
                    ),
                ]
            )
        ),
    )


def _selected_configuration_items(
    draft: QuotationDraft,
    recommendation: QuoteRecommendation | None,
) -> tuple[RecommendationItem, ...]:
    if recommendation is None:
        return ()
    selected_ids = set(draft.selected_product_ids)
    selected_main = tuple(
        item
        for item in (
            recommendation.main_model,
            *recommendation.alternatives,
        )
        if item is not None and item.product_id in selected_ids
    )
    return (*selected_main, *recommendation.accessories)


def _product_id_for_step(
    items: tuple[RecommendationItem, ...],
    expected_step: str,
) -> str | None:
    for item in items:
        if _step_suffix(item.step_id) == expected_step:
            return item.product_id
    return None


def _step_suffix(step_id: str | None) -> str:
    if not step_id:
        return ""
    match = re.search(r"(?:step[_ ]?)(\d+[a-z]?)$", step_id.casefold())
    return match.group(1) if match else ""


def _infer_system_family(text: str) -> str | None:
    normalized = text.casefold()
    if any(value in normalized for value in ("floor mount", "fmt")):
        return "FMT"
    if any(value in normalized for value in ("overhead", "otc")):
        return "OTC"
    return None


def _infer_acquisition_type(text: str) -> str | None:
    normalized = text.casefold()
    if "digital" in normalized or "drx" in normalized:
        return "digital"
    if "analog" in normalized or "analogue" in normalized:
        return "analog"
    return None


def _infer_detector_type(text: str) -> str | None:
    normalized = text.casefold()
    detector_patterns = (
        ("Focus 35C", r"\bfocus\s*35c?\b"),
        ("Focus 43C", r"\bfocus\s*43c?\b"),
        ("Focus HD", r"\bfocus\s*hd\b"),
        ("DRX Plus/Lux", r"\bdrx\s*(?:plus|lux)\b"),
        ("DRX LC", r"\bdrx\s*lc\b"),
    )
    for detector, pattern in detector_patterns:
        if re.search(pattern, normalized):
            return detector
    return None


def _infer_grid_position(product_query: str, descriptions: str) -> str | None:
    normalized = f"{product_query} {descriptions}".casefold()
    has_table = bool(re.search(r"\btable\b", normalized))
    has_wall = bool(re.search(r"\b(?:wallstand|wall stand)\b", normalized))
    if has_table and not has_wall:
        return "table"
    if has_wall and not has_table:
        return "wall"
    return None


def _infer_generator(
    descriptions: str,
    engine: QuotationRuleEngine,
) -> str | None:
    normalized = _compact(descriptions)
    generators = {
        spec.generator
        for spec in engine.snapshot.generator_tube_specs
        if spec.generator
    }
    return next(
        (
            generator
            for generator in sorted(generators, key=len, reverse=True)
            if _compact(generator) in normalized
        ),
        None,
    )


def _infer_tube_spec(
    descriptions: str,
    generator: str | None,
    engine: QuotationRuleEngine,
) -> str | None:
    if not generator:
        return None
    normalized = _compact(descriptions)
    specs = engine.snapshot.generator_specs(generator)
    return next(
        (
            spec.tube_spec
            for spec in sorted(specs, key=lambda item: len(item.tube_spec), reverse=True)
            if _compact(spec.tube_spec) in normalized
        ),
        None,
    )


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())
