from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})
PRICING_DATA_MODES = frozenset({"synthetic", "archived_workbook"})

REQUIRED_QUOTATION_FIELDS = (
    "customer_name",
    "region",
    "product_query",
    "quantity",
    "currency",
    "incoterm",
    "delivery_location",
)

# Demo assumptions only. These are not approved commercial policies.
SAP_BASE_CURRENCY = "USD"
DEMO_MIN_GROSS_MARGIN_PERCENT = 15.0
DEMO_REVIEW_MARGIN_PERCENT = 20.0
DEMO_AUTO_DISCOUNT_LIMIT_PERCENT = 10.0
DEMO_MANAGER_DISCOUNT_LIMIT_PERCENT = 20.0
DEMO_PRICE_DEVIATION_REVIEW_PERCENT = 15.0
DEMO_PRICE_DEVIATION_BLOCK_PERCENT = 30.0
DEMO_QUOTATION_VALIDITY_DAYS = 30
DEMO_QUANTITY_DISCOUNT_POLICY = (
    (1, 1, 0.0),
    (2, 4, 2.0),
    (5, 9, 4.0),
    (10, None, 6.0),
)

CUSTOMER_PROHIBITED_FIELDS = frozenset(
    {
        "approval",
        "approval_reasons",
        "audit_events",
        "cell",
        "checked_rules",
        "cogs",
        "commercial_validation",
        "comparable_count",
        "cost",
        "estimated_cost",
        "evaluated_rules",
        "excel_cell",
        "excel_sheet",
        "gross_margin_amount",
        "gross_margin_percent",
        "internal_email",
        "internal_evidence",
        "internal_margin_threshold",
        "margin_threshold",
        "match_reasons",
        "match_score",
        "minimum_margin",
        "minimum_price",
        "minimum_price_policy",
        "net_price",
        "reference_net_price",
        "rule_artifact",
        "rule_artifact_filename",
        "rule_filename",
        "rule_id",
        "sheet",
        "source",
        "source_id",
        "source_sheet",
        "workbook",
        "workbook_path",
    }
)


def _environment_flag(
    name: str,
    default: bool,
    environment: Mapping[str, str],
) -> bool:
    raw_value = environment.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().casefold()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class AppConfig:
    demo_mode: bool = True
    show_internal_costs: bool = False
    enable_llm: bool = False
    pricing_data_mode: str = "synthetic"


def load_config(environment: Mapping[str, str] | None = None) -> AppConfig:
    values = os.environ if environment is None else environment
    return AppConfig(
        demo_mode=_environment_flag("DEMO_MODE", True, values),
        show_internal_costs=_environment_flag("SHOW_INTERNAL_COSTS", False, values),
        enable_llm=_environment_flag("ENABLE_LLM", False, values),
        pricing_data_mode=_environment_choice(
            "PRICING_DATA_MODE",
            "synthetic",
            PRICING_DATA_MODES,
            values,
        ),
    )


def _environment_choice(
    name: str,
    default: str,
    allowed_values: frozenset[str],
    environment: Mapping[str, str],
) -> str:
    value = environment.get(name, default).strip().casefold()
    if value not in allowed_values:
        choices = ", ".join(sorted(allowed_values))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


CONFIG = load_config()
DEMO_MODE = CONFIG.demo_mode
SHOW_INTERNAL_COSTS = CONFIG.show_internal_costs
ENABLE_LLM = CONFIG.enable_llm
PRICING_DATA_MODE = CONFIG.pricing_data_mode
