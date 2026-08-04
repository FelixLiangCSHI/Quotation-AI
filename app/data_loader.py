from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from app.models import (
    DetectorGridSupport,
    GeneratorTubeSpec,
    Product,
    RuleSignal,
    StepOption,
    SystemCompatibility,
)


DETECTOR_GRID_SUPPORT_BY_COLUMN = {
    "F": ("position", "Table"),
    "G": ("position", "Wall"),
    "H": ("detector", "DRX Plus/Lux"),
    "I": ("detector", "Focus 35C"),
    "J": ("detector", "Focus 43C"),
    "K": ("detector", "Focus HD"),
    "L": ("detector", "DRX LC"),
}


def default_snapshot_path() -> Path:
    return Path(__file__).resolve().parents[1] / "quotation_snapshot.json"


def synthetic_snapshot_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "Data"
        / "synthetic"
        / "quotation_snapshot.json"
    )


class QuotationSnapshot:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.products = tuple(_parse_product(item) for item in raw.get("products", []))
        self.step_options = tuple(
            _parse_step_option(item) for item in raw.get("step_options", [])
        )
        self.rule_signals = tuple(
            _parse_rule_signal(item) for item in raw.get("rule_signals", [])
        )
        self.compatibility_matrix = tuple(
            _parse_system_compatibility(item)
            for item in raw.get("compatibility_matrix", [])
        )
        self.detector_grid_supports = tuple(
            support
            for support in (
                _parse_detector_grid_support(item)
                for item in raw.get("detector_grid_matrix", [])
            )
            if support is not None
        )
        self.generator_tube_specs = tuple(
            spec
            for spec in (
                _parse_generator_tube_spec(item)
                for item in raw.get("generator_tube_matrix", [])
            )
            if spec is not None
        )

        self.products_by_id = {product.product_id: product for product in self.products}
        self.step_options_by_product_id = {
            option.product_id: option for option in self.step_options
        }
        self.rules_by_product_id = _group_rules_by_product(self.rule_signals)
        self.compatibility_by_key = {
            _compatibility_key(
                item.system_family,
                item.acquisition_type,
                item.tube_stand_id,
                item.wallstand_id,
                item.table_id,
            ): item
            for item in self.compatibility_matrix
        }
        self.detector_grid_by_grid_id = _group_detector_grid_supports(
            self.detector_grid_supports
        )
        self.detector_grid_by_key = {
            _detector_grid_key(item.grid_id, item.support_kind, item.support_name): item
            for item in self.detector_grid_supports
        }
        self.generator_tube_by_generator = _group_generator_tube_specs(
            self.generator_tube_specs
        )
        self.generator_tube_by_key = {
            _generator_tube_key(item.generator, item.spec_category, item.tube_spec): item
            for item in self.generator_tube_specs
        }
        self.generator_tube_by_tube_key = _group_generator_tube_specs_by_tube(
            self.generator_tube_specs
        )

    def find_products(self, query: str, limit: int = 10) -> tuple[Product, ...]:
        normalized_query = query.casefold().strip()
        if not normalized_query:
            return ()

        matches: list[Product] = []
        for product in self.products:
            haystack = " ".join(
                item
                for item in (
                    product.product_id,
                    product.short_description,
                    product.comments or "",
                    product.jicheng_comments or "",
                )
                if item
            ).casefold()
            if normalized_query in haystack:
                matches.append(product)
                if len(matches) >= limit:
                    break
        return tuple(matches)

    def rules_for_product(self, product_id: str) -> tuple[RuleSignal, ...]:
        return tuple(self.rules_by_product_id.get(product_id, ()))

    def find_system_compatibility(
        self,
        system_family: str,
        acquisition_type: str,
        tube_stand_id: str,
        wallstand_id: str,
        table_id: str,
    ) -> SystemCompatibility | None:
        return self.compatibility_by_key.get(
            _compatibility_key(
                system_family, acquisition_type, tube_stand_id, wallstand_id, table_id
            )
        )

    def find_detector_grid_support(
        self, grid_id: str, support_kind: str, support_name: str
    ) -> DetectorGridSupport | None:
        return self.detector_grid_by_key.get(
            _detector_grid_key(grid_id, support_kind, support_name)
        )

    def detector_grid_supports_for_grid(
        self, grid_id: str
    ) -> tuple[DetectorGridSupport, ...]:
        return tuple(self.detector_grid_by_grid_id.get(grid_id.casefold().strip(), ()))

    def has_grid(self, grid_id: str) -> bool:
        return grid_id.casefold().strip() in self.detector_grid_by_grid_id

    def has_generator(self, generator: str) -> bool:
        return generator.casefold().strip() in self.generator_tube_by_generator

    def generator_specs(self, generator: str) -> tuple[GeneratorTubeSpec, ...]:
        return tuple(
            self.generator_tube_by_generator.get(generator.casefold().strip(), ())
        )

    def find_generator_tube_spec(
        self, generator: str, spec_category: str, tube_spec: str
    ) -> GeneratorTubeSpec | None:
        return self.generator_tube_by_key.get(
            _generator_tube_key(generator, spec_category, tube_spec)
        )

    def generator_tube_specs_for_tube(
        self, generator: str, tube_spec: str
    ) -> tuple[GeneratorTubeSpec, ...]:
        return tuple(
            self.generator_tube_by_tube_key.get(
                _generator_tube_tube_key(generator, tube_spec), ()
            )
        )


def load_snapshot(path: str | Path | None = None) -> QuotationSnapshot:
    snapshot_path = Path(path) if path else default_snapshot_path()
    with snapshot_path.open("r", encoding="utf-8") as snapshot_file:
        raw = json.load(snapshot_file)
    if not isinstance(raw, dict):
        raise ValueError("Snapshot root must be a JSON object.")
    return QuotationSnapshot(raw)


def _parse_product(item: dict[str, Any]) -> Product:
    return Product(
        product_id=str(item.get("product_id", "")).strip(),
        system_family=_optional_str(item.get("system_family")),
        category=_optional_str(item.get("category")),
        short_description=str(item.get("short_description") or "").strip(),
        comments=_optional_str(item.get("comments")),
        jicheng_comments=_optional_str(item.get("jicheng_comments")),
        source=dict(item.get("source") or {}),
    )


def _parse_step_option(item: dict[str, Any]) -> StepOption:
    return StepOption(
        step_id=_optional_str(item.get("step_id")),
        product_id=str(item.get("product_id", "")).strip(),
        option_group=_optional_str(item.get("option_group")),
        short_description=str(item.get("short_description") or "").strip(),
        raw_constraint_text=_optional_str(item.get("raw_constraint_text")),
        source=dict(item.get("source") or {}),
    )


def _parse_rule_signal(item: dict[str, Any]) -> RuleSignal:
    return RuleSignal(
        rule_id=str(item.get("rule_id", "")).strip(),
        product_id=_optional_str(item.get("product_id")),
        step_id=_optional_str(item.get("step_id")),
        applies_to_step_id=_optional_str(item.get("applies_to_step_id")),
        rule_type=str(item.get("rule_type") or "unknown").strip(),
        strength=str(item.get("strength") or "unknown").strip(),
        review_status=str(item.get("review_status") or "needs_review").strip(),
        confidence=float(item.get("confidence") or 0),
        condition_text=str(item.get("condition_text") or "").strip(),
        message=str(item.get("message") or "").strip(),
        regions=tuple(str(region).strip() for region in item.get("regions", []) if region),
        source=dict(item.get("source") or {}),
    )


def _parse_system_compatibility(item: dict[str, Any]) -> SystemCompatibility:
    return SystemCompatibility(
        matrix_name=str(item.get("matrix_name") or "").strip(),
        system_family=str(item.get("system_family") or "").strip(),
        acquisition_type=str(item.get("acquisition_type") or "").strip(),
        tube_stand_id=str(item.get("tube_stand_id") or "").strip(),
        tube_stand_name=_optional_str(item.get("tube_stand_name")),
        wallstand_id=str(item.get("wallstand_id") or "").strip(),
        wallstand_name=_optional_str(item.get("wallstand_name")),
        table_id=str(item.get("table_id") or "").strip(),
        table_name=_optional_str(item.get("table_name")),
        status=str(item.get("status") or "unknown").strip(),
        signal_text=_optional_str(item.get("signal_text")),
        remark=_optional_str(item.get("remark")),
        source=dict(item.get("source") or {}),
    )


def _parse_detector_grid_support(item: dict[str, Any]) -> DetectorGridSupport | None:
    source = dict(item.get("source") or {})
    support_kind = _optional_str(item.get("support_kind"))
    support_name = _optional_str(item.get("support_name"))
    support = (
        (support_kind, support_name)
        if support_kind and support_name
        else DETECTOR_GRID_SUPPORT_BY_COLUMN.get(
            _cell_column(str(source.get("cell") or ""))
        )
    )
    if support is None:
        return None
    support_kind, support_name = support
    return DetectorGridSupport(
        grid_id=str(item.get("grid_id") or "").strip(),
        grid_description=str(item.get("grid_description") or "").strip(),
        support_kind=support_kind,
        support_name=support_name,
        support_value=str(item.get("support_value") or "").strip(),
        source=source,
    )


def _parse_generator_tube_spec(item: dict[str, Any]) -> GeneratorTubeSpec | None:
    generator = _optional_str(item.get("generator"))
    tube_spec = _optional_str(item.get("tube_spec"))
    value = _optional_str(item.get("value"))
    if not generator or not tube_spec or not value:
        return None
    return GeneratorTubeSpec(
        spec_category=_generator_spec_category(item),
        tube_spec=tube_spec,
        generator=generator,
        value=value,
        source=dict(item.get("source") or {}),
    )


def _group_rules_by_product(rules: Iterable[RuleSignal]) -> dict[str, list[RuleSignal]]:
    grouped: dict[str, list[RuleSignal]] = defaultdict(list)
    for rule in rules:
        if rule.product_id:
            grouped[rule.product_id].append(rule)
    return dict(grouped)


def _group_detector_grid_supports(
    supports: Iterable[DetectorGridSupport],
) -> dict[str, list[DetectorGridSupport]]:
    grouped: dict[str, list[DetectorGridSupport]] = defaultdict(list)
    for support in supports:
        grouped[support.grid_id.casefold().strip()].append(support)
    return dict(grouped)


def _group_generator_tube_specs(
    specs: Iterable[GeneratorTubeSpec],
) -> dict[str, list[GeneratorTubeSpec]]:
    grouped: dict[str, list[GeneratorTubeSpec]] = defaultdict(list)
    for spec in specs:
        grouped[spec.generator.casefold().strip()].append(spec)
    return dict(grouped)


def _group_generator_tube_specs_by_tube(
    specs: Iterable[GeneratorTubeSpec],
) -> dict[tuple[str, str], list[GeneratorTubeSpec]]:
    grouped: dict[tuple[str, str], list[GeneratorTubeSpec]] = defaultdict(list)
    for spec in specs:
        grouped[_generator_tube_tube_key(spec.generator, spec.tube_spec)].append(spec)
    return dict(grouped)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compatibility_key(
    system_family: str,
    acquisition_type: str,
    tube_stand_id: str,
    wallstand_id: str,
    table_id: str,
) -> tuple[str, str, str, str, str]:
    return (
        system_family.casefold().strip(),
        acquisition_type.casefold().strip(),
        tube_stand_id.casefold().strip(),
        wallstand_id.casefold().strip(),
        table_id.casefold().strip(),
    )


def _detector_grid_key(
    grid_id: str, support_kind: str, support_name: str
) -> tuple[str, str, str]:
    return (
        grid_id.casefold().strip(),
        support_kind.casefold().strip(),
        support_name.casefold().strip(),
    )


def _generator_tube_key(
    generator: str, spec_category: str, tube_spec: str
) -> tuple[str, str, str]:
    return (
        generator.casefold().strip(),
        spec_category.casefold().strip(),
        tube_spec.casefold().strip(),
    )


def _generator_tube_tube_key(generator: str, tube_spec: str) -> tuple[str, str]:
    return (generator.casefold().strip(), tube_spec.casefold().strip())


def _generator_spec_category(item: dict[str, Any]) -> str:
    explicit_category = _optional_str(item.get("spec_category"))
    if explicit_category:
        return explicit_category
    source = dict(item.get("source") or {})
    row = _cell_row(str(source.get("cell") or ""))
    if row == 4:
        return "phase_line"
    if row == 5:
        return "rated_voltage"
    if row == 6:
        return "momentary_current"
    if 8 <= row <= 10:
        return "output_kw_at_100ma"
    if 12 <= row <= 14:
        return "kvp_range"
    if 16 <= row <= 18:
        return "ma_range"
    if 20 <= row <= 22:
        return "mas_range"
    if row == 23:
        return "time_range_s"
    return "unknown"


def _cell_column(cell: str) -> str:
    return "".join(character for character in cell.upper() if character.isalpha())


def _cell_row(cell: str) -> int:
    digits = "".join(character for character in cell if character.isdigit())
    return int(digits) if digits else 0
