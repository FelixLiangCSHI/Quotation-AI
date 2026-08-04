from __future__ import annotations

import json
from pathlib import Path

from app.data_loader import QuotationSnapshot
from app.models import RuleSignal, ValidationIssue, ValidationResult


REGION_ALIASES = {
    "c": "canada",
    "ca": "canada",
    "canada": "canada",
    "cn": "china",
    "china": "china",
    "prc": "china",
    "u.s.": "us",
    "usa": "us",
    "united states": "us",
    "us": "us",
}

DETECTOR_ALIASES = {
    "drx plus": "drx plus/lux",
    "drx plus/lux": "drx plus/lux",
    "drx lux": "drx plus/lux",
    "focus 35c": "focus 35c",
    "focus 43c": "focus 43c",
    "focus hd": "focus hd",
    "drx lc": "drx lc",
}

GRID_POSITION_ALIASES = {
    "table": "table",
    "wall": "wall",
    "wallstand": "wall",
    "wall stand": "wall",
}

GENERATOR_SPEC_CATEGORY_ALIASES = {
    "current": "momentary_current",
    "kvp": "kvp_range",
    "kvp range": "kvp_range",
    "kvp_range": "kvp_range",
    "ma": "ma_range",
    "ma range": "ma_range",
    "ma_range": "ma_range",
    "mas": "mas_range",
    "mas range": "mas_range",
    "mas_range": "mas_range",
    "momentary current": "momentary_current",
    "momentary_current": "momentary_current",
    "output": "output_kw_at_100ma",
    "output kw": "output_kw_at_100ma",
    "output kw at 100ma": "output_kw_at_100ma",
    "output_kw_at_100ma": "output_kw_at_100ma",
    "phase": "phase_line",
    "phase line": "phase_line",
    "phase_line": "phase_line",
    "rated voltage": "rated_voltage",
    "rated voltage (everest)": "rated_voltage",
    "rated_voltage": "rated_voltage",
    "time": "time_range_s",
    "time range": "time_range_s",
    "time_range_s": "time_range_s",
    "voltage": "rated_voltage",
}


class QuotationRuleEngine:
    def __init__(self, snapshot: QuotationSnapshot) -> None:
        self.snapshot = snapshot
        self.known_product_ids = set(snapshot.products_by_id) | _load_decision_tree_product_ids()

    def check_configuration(
        self,
        product_ids: list[str] | tuple[str, ...],
        region: str | None = None,
        system_family: str | None = None,
        acquisition_type: str | None = None,
        tube_stand_id: str | None = None,
        wallstand_id: str | None = None,
        table_id: str | None = None,
        grid_id: str | None = None,
        grid_position: str | None = None,
        detector_type: str | None = None,
        generator: str | None = None,
        tube_spec: str | None = None,
        spec_category: str | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        missing_fields: list[str] = []
        normalized_region = _normalize_region(region) if region else None
        compatibility_fields = {
            "system_family": system_family,
            "acquisition_type": acquisition_type,
            "tube_stand_id": tube_stand_id,
            "wallstand_id": wallstand_id,
            "table_id": table_id,
        }
        provided_compatibility_fields = [
            name for name, value in compatibility_fields.items() if value
        ]
        detector_grid_fields = {
            "grid_id": grid_id,
            "grid_position": grid_position,
            "detector_type": detector_type,
        }
        provided_detector_grid_fields = [
            name for name, value in detector_grid_fields.items() if value
        ]
        generator_tube_fields = {
            "generator": generator,
            "tube_spec": tube_spec,
            "spec_category": spec_category,
        }
        provided_generator_tube_fields = [
            name for name, value in generator_tube_fields.items() if value
        ]

        if (
            not product_ids
            and not provided_compatibility_fields
            and not provided_detector_grid_fields
            and not provided_generator_tube_fields
        ):
            missing_fields.append("product_ids")

        if product_ids and not normalized_region:
            missing_fields.append("region")

        for product_id in product_ids:
            product = self.snapshot.products_by_id.get(product_id)
            if not product and product_id not in self.known_product_ids:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="unknown_product",
                        message=f"Unknown product id: {product_id}",
                        product_id=product_id,
                    )
                )
                continue

            if product and normalized_region:
                issues.extend(
                    self._check_product_region_rules(product_id, normalized_region)
                )

        if provided_compatibility_fields:
            missing_compatibility_fields = [
                name for name, value in compatibility_fields.items() if not value
            ]
            optional_component_match = (
                system_family
                and acquisition_type
                and self.snapshot.find_system_compatibility(
                    system_family,
                    acquisition_type,
                    tube_stand_id or "",
                    wallstand_id or "",
                    table_id or "",
                )
            )
            if not missing_compatibility_fields or optional_component_match:
                issues.extend(
                    self._check_system_compatibility(
                        system_family=system_family or "",
                        acquisition_type=acquisition_type or "",
                        tube_stand_id=tube_stand_id or "",
                        wallstand_id=wallstand_id or "",
                        table_id=table_id or "",
                    )
                )
            else:
                missing_fields.extend(missing_compatibility_fields)

        if provided_detector_grid_fields:
            if not grid_id:
                missing_fields.append("grid_id")
            elif grid_position or detector_type:
                issues.extend(
                    self._check_detector_grid_support(
                        grid_id=grid_id,
                        grid_position=grid_position,
                        detector_type=detector_type,
                    )
                )

        if provided_generator_tube_fields:
            if not generator:
                missing_fields.append("generator")
            elif spec_category and not tube_spec:
                missing_fields.append("tube_spec")
            elif not tube_spec:
                issues.extend(self._check_generator_known(generator))
            else:
                issues.extend(
                    self._check_generator_tube_spec(
                        generator, tube_spec, spec_category=spec_category
                    )
                )

        blocking_issues = [issue for issue in issues if issue.severity == "error"]
        if blocking_issues:
            status = "invalid"
        elif missing_fields:
            status = "incomplete"
        else:
            status = "valid"

        return ValidationResult(
            status=status,
            issues=tuple(issues),
            missing_fields=tuple(dict.fromkeys(missing_fields)),
        )

    def product_constraints(self, product_id: str) -> tuple[RuleSignal, ...]:
        return self.snapshot.rules_for_product(product_id)

    def _check_product_region_rules(
        self, product_id: str, normalized_region: str
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen_rules: set[tuple[str, tuple[str, ...], str]] = set()
        for rule in self.snapshot.rules_for_product(product_id):
            allowed_regions = _allowed_regions(rule)
            if not allowed_regions:
                continue
            if normalized_region in allowed_regions:
                continue
            dedupe_key = (product_id, tuple(sorted(allowed_regions)), rule.message)
            if dedupe_key in seen_rules:
                continue
            seen_rules.add(dedupe_key)
            issues.append(
                ValidationIssue(
                    severity="error" if rule.strength == "hard_block" else "warning",
                    code="region_not_allowed",
                    message=(
                        f"Product {product_id} is limited to "
                        f"{', '.join(sorted(allowed_regions))}: {rule.message}"
                    ),
                    product_id=product_id,
                    rule_id=rule.rule_id,
                    source=rule.source,
                )
            )
        return issues

    def _check_system_compatibility(
        self,
        system_family: str,
        acquisition_type: str,
        tube_stand_id: str,
        wallstand_id: str,
        table_id: str,
    ) -> list[ValidationIssue]:
        compatibility = self.snapshot.find_system_compatibility(
            system_family, acquisition_type, tube_stand_id, wallstand_id, table_id
        )
        if compatibility is None:
            return [
                ValidationIssue(
                    severity="warning",
                    code="compatibility_not_found",
                    message=(
                        "No system compatibility matrix row matched "
                        f"{system_family}/{acquisition_type} with tube stand "
                        f"{tube_stand_id}, wall stand {wallstand_id}, table {table_id}."
                    ),
                )
            ]

        status = compatibility.status.casefold().strip()
        if status == "supported":
            return []

        if status == "not_supported":
            severity = "error"
            code = "system_not_supported"
        else:
            severity = "warning"
            code = "system_conditionally_supported"

        detail = compatibility.remark or compatibility.signal_text or compatibility.status
        return [
            ValidationIssue(
                severity=severity,
                code=code,
                message=(
                    f"System combination is {compatibility.status}: {detail}"
                ),
                source=compatibility.source,
            )
        ]

    def _check_detector_grid_support(
        self,
        grid_id: str,
        grid_position: str | None,
        detector_type: str | None,
    ) -> list[ValidationIssue]:
        if not self.snapshot.has_grid(grid_id):
            return [
                ValidationIssue(
                    severity="error",
                    code="unknown_grid",
                    message=f"Unknown grid id in detector/grid matrix: {grid_id}",
                )
            ]

        issues: list[ValidationIssue] = []
        grid_supports = self.snapshot.detector_grid_supports_for_grid(grid_id)
        grid_description = grid_supports[0].grid_description if grid_supports else grid_id
        if grid_position:
            position = _normalize_grid_position(grid_position)
            if not self.snapshot.find_detector_grid_support(grid_id, "position", position):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="grid_position_not_supported",
                        message=(
                            f"Grid {grid_id} ({grid_description}) does not support "
                            f"{position} position."
                        ),
                    )
                )

        if detector_type:
            detector = _normalize_detector_type(detector_type)
            if not self.snapshot.find_detector_grid_support(grid_id, "detector", detector):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="detector_grid_not_supported",
                        message=(
                            f"Grid {grid_id} ({grid_description}) does not support "
                            f"detector {detector}."
                        ),
                    )
                )
        return issues

    def _check_generator_known(self, generator: str) -> list[ValidationIssue]:
        if self.snapshot.has_generator(generator):
            return []
        return [
            ValidationIssue(
                severity="error",
                code="unknown_generator",
                message=f"Unknown generator in generator/tube spec table: {generator}",
            )
        ]

    def _check_generator_tube_spec(
        self, generator: str, tube_spec: str, spec_category: str | None
    ) -> list[ValidationIssue]:
        known_generator_issues = self._check_generator_known(generator)
        if known_generator_issues:
            return known_generator_issues

        normalized_category = (
            _normalize_generator_spec_category(spec_category) if spec_category else None
        )
        if normalized_category:
            spec = self.snapshot.find_generator_tube_spec(
                generator, normalized_category, tube_spec
            )
            specs = (spec,) if spec else ()
        else:
            specs = self.snapshot.generator_tube_specs_for_tube(generator, tube_spec)

        if not specs:
            category_detail = f" in {normalized_category}" if normalized_category else ""
            return [
                ValidationIssue(
                    severity="error",
                    code="generator_tube_spec_not_found",
                    message=(
                        f"Generator {generator} has no recorded spec for "
                        f"{tube_spec}{category_detail}."
                    ),
                )
            ]

        return [
            ValidationIssue(
                severity="info",
                code="generator_tube_spec_found",
                message=(
                    f"Generator {generator} / {spec.spec_category} / "
                    f"{tube_spec}: {spec.value}"
                ),
            )
            for spec in specs
        ]


def _allowed_regions(rule: RuleSignal) -> set[str]:
    if not rule.regions:
        return set()
    if "only" not in rule.message.casefold() and rule.rule_type != "region_only":
        return set()
    return {_normalize_region(region) for region in rule.regions}


def _normalize_region(region: str) -> str:
    normalized = region.casefold().strip()
    return REGION_ALIASES.get(normalized, normalized)


def _normalize_detector_type(detector_type: str) -> str:
    normalized = detector_type.casefold().strip()
    return DETECTOR_ALIASES.get(normalized, normalized)


def _normalize_grid_position(grid_position: str) -> str:
    normalized = grid_position.casefold().strip()
    return GRID_POSITION_ALIASES.get(normalized, normalized)


def _normalize_generator_spec_category(spec_category: str) -> str:
    normalized = spec_category.casefold().strip()
    return GENERATOR_SPEC_CATEGORY_ALIASES.get(normalized, normalized)


def _load_decision_tree_product_ids() -> set[str]:
    path = Path(__file__).resolve().parents[1] / "rules" / "decision_tree_normalized_rules.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(product.get("product_id") or "").strip()
        for product in data.get("products", [])
        if str(product.get("product_id") or "").strip()
    }
