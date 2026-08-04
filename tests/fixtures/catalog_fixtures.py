"""Synthetic catalogue fixtures for Phase 4 requirement and line-item tests.

No real product, price or customer data appears here.
"""

from __future__ import annotations

from typing import Any

from app.data_loader import QuotationSnapshot
from app.recommender import QuoteRecommender
from app.rule_engine import QuotationRuleEngine


def synthetic_snapshot_payload() -> dict[str, Any]:
    return {
        "products": [
            {
                "product_id": "SYN-MAIN-1",
                "system_family": "SynFamily",
                "category": "system",
                "short_description": "Synthetic imaging system console",
            },
            {
                "product_id": "SYN-ACC-1",
                "system_family": "SynFamily",
                "category": "accessory",
                "short_description": "Synthetic detector grid",
            },
            {
                "product_id": "SYN-ACC-2",
                "system_family": "SynFamily",
                "category": "accessory",
                "short_description": "Synthetic wall stand",
            },
            {
                "product_id": "SYN-REGION-ONLY",
                "system_family": "SynFamily",
                "category": "accessory",
                "short_description": "Synthetic region-restricted accessory",
            },
        ],
        "step_options": [
            {
                "step_id": "syn_step_1a",
                "product_id": "SYN-MAIN-1",
                "short_description": "Synthetic imaging system console",
            },
            {
                "step_id": "syn_step_10",
                "product_id": "SYN-ACC-1",
                "short_description": "Synthetic detector grid",
            },
            {
                "step_id": "syn_step_9a",
                "product_id": "SYN-ACC-2",
                "short_description": "Synthetic wall stand",
            },
        ],
        "rule_signals": [
            {
                "rule_id": "SYN-RULE-1",
                "product_id": "SYN-REGION-ONLY",
                "rule_type": "region_only",
                "strength": "hard_block",
                "review_status": "confirmed",
                "confidence": 1.0,
                "message": "Available only in the synthetic canada region.",
                "regions": ["canada"],
            }
        ],
    }


def synthetic_snapshot() -> QuotationSnapshot:
    return QuotationSnapshot(synthetic_snapshot_payload())


def synthetic_rule_engine() -> QuotationRuleEngine:
    return QuotationRuleEngine(synthetic_snapshot())


def synthetic_recommender() -> QuoteRecommender:
    return QuoteRecommender(snapshot=synthetic_snapshot(), profile_products={})
