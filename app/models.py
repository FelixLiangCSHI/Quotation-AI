from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Product:
    product_id: str
    system_family: str | None
    category: str | None
    short_description: str
    comments: str | None
    jicheng_comments: str | None
    source: dict[str, Any]


@dataclass(frozen=True)
class StepOption:
    step_id: str | None
    product_id: str
    option_group: str | None
    short_description: str
    raw_constraint_text: str | None
    source: dict[str, Any]


@dataclass(frozen=True)
class RuleSignal:
    rule_id: str
    rule_type: str
    strength: str
    review_status: str
    confidence: float
    condition_text: str
    message: str
    product_id: str | None = None
    step_id: str | None = None
    applies_to_step_id: str | None = None
    regions: tuple[str, ...] = ()
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SystemCompatibility:
    matrix_name: str
    system_family: str
    acquisition_type: str
    tube_stand_id: str
    tube_stand_name: str | None
    wallstand_id: str
    wallstand_name: str | None
    table_id: str
    table_name: str | None
    status: str
    signal_text: str | None
    remark: str | None
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectorGridSupport:
    grid_id: str
    grid_description: str
    support_kind: str
    support_name: str
    support_value: str
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratorTubeSpec:
    spec_category: str
    tube_spec: str
    generator: str
    value: str
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    product_id: str | None = None
    rule_id: str | None = None
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    status: str
    issues: tuple[ValidationIssue, ...]
    missing_fields: tuple[str, ...] = ()
