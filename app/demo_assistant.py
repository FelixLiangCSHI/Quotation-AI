"""Local, rule-based chat assistant for the demo quotation page.

The assistant reuses the existing offline natural-language capability:

* :func:`app.natural_language.parse_quote_request` extracts the requirement;
* :class:`app.recommender.QuoteRecommender` matches the configuration.

No external AI API is called and no API key is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.data_loader import (
    QuotationSnapshot,
    default_snapshot_path,
    load_snapshot,
    synthetic_snapshot_path,
)
from app.demo_quotation import (
    DISCOUNT_APPROVAL_THRESHOLD,
    build_configuration_description,
    missing_configuration_fields,
    normalize_configuration,
    parse_discount_rate,
)
from app.natural_language import parse_quote_request
from app.recommender import QuoteRecommender

GREETING = (
    "Hi, I am your quotation assistant. Tell me what the customer needs, "
    "for example: *Quote 2 digital floor mounted X-ray systems for "
    "Singapore General Hospital in USD*."
)

FIELD_QUESTIONS = {
    "main_product": (
        "Which system should I configure? You can describe it in your own "
        "words, for example a digital floor mounted X-ray system."
    ),
    "customer_name": "Which customer is this quotation for?",
    "region": "Which region or market is this quotation for?",
    "quantity": "How many systems does the customer need?",
}

DISCOUNT_QUESTION = "What discount rate would you like to apply?"

#: Keywords that indicate the turn is about the product configuration.
_PRODUCT_SIGNAL_KEYWORDS = frozenset(
    {
        "x-ray",
        "system",
        "detector",
        "generator",
        "tube",
        "collimator",
        "wallstand",
        "table",
        "grid",
        "bucky",
        "wireless",
        "motorized",
        "manual",
        "focus",
        "drx",
        "compass",
    }
)


def _has_product_signal(request: Any, previous: Mapping[str, Any] | None) -> bool:
    """True when the turn says something about the product configuration."""

    if not (previous or {}).get("main_product"):
        # Nothing configured yet, so always let the recommender try.
        return True
    if request.product_ids or request.system_family or request.acquisition_type:
        return True
    return bool(_PRODUCT_SIGNAL_KEYWORDS.intersection(request.keywords or ()))


def load_demo_snapshot() -> QuotationSnapshot:
    """Load the real snapshot when present, otherwise the synthetic one."""

    path = default_snapshot_path()
    if not path.exists():
        path = synthetic_snapshot_path()
    return load_snapshot(path)


@dataclass
class TurnResult:
    """What one chat turn produced."""

    reply: str
    configuration: dict[str, Any] = field(default_factory=dict)
    configuration_ready: bool = False
    discount_rate: float | None = None


class DemoQuotationAssistant:
    """Multi-turn assistant driving the demo conversation."""

    def __init__(self, recommender: QuoteRecommender | None = None) -> None:
        self.recommender = recommender or QuoteRecommender(
            snapshot=load_demo_snapshot()
        )

    def handle_message(
        self,
        message: str,
        configuration: Mapping[str, Any] | None = None,
    ) -> TurnResult:
        text = (message or "").strip()
        if not text:
            return TurnResult(
                reply="Please tell me what the customer needs.",
                configuration=dict(configuration or {}),
            )

        request = parse_quote_request(text)
        # A turn that carries no product signal (for example "40%" or
        # "Singapore") must not re-run the selection, otherwise the confirmed
        # configuration would drift between turns.
        recommendation = (
            self.recommender.recommend(request)
            if _has_product_signal(request, configuration)
            else None
        )
        config = normalize_configuration(request, recommendation, configuration)

        # A short answer such as "Singapore" or "40%" answers the previous
        # question, so the conversation stays multi-turn without an LLM.
        self._apply_short_answer(text, request, config, configuration)
        config["configuration_description"] = build_configuration_description(
            config
        )

        missing = missing_configuration_fields(config)
        if missing:
            reply = self._configuration_summary(config, partial=True)
            reply += "\n\n" + FIELD_QUESTIONS[missing[0]]
            return TurnResult(reply=reply, configuration=config)

        reply = self._configuration_summary(config)
        if config.get("discount_rate") is None:
            reply += "\n\n" + DISCOUNT_QUESTION
            return TurnResult(
                reply=reply, configuration=config, configuration_ready=True
            )

        rate = float(config["discount_rate"])
        reply += (
            f"\n\nI applied a {rate:.1%} discount and prepared the quotation "
            "on the right. You can still edit quantity and quotation unit "
            "price in the table."
        )
        if rate > DISCOUNT_APPROVAL_THRESHOLD:
            reply += (
                f"\n\nThis exceeds the {DISCOUNT_APPROVAL_THRESHOLD:.0%} "
                "Sales approval authority, so manager approval is required."
            )
        else:
            reply += (
                f"\n\nThis is within the {DISCOUNT_APPROVAL_THRESHOLD:.0%} "
                "Sales approval authority, so the quotation is automatically "
                "approved."
            )
        return TurnResult(
            reply=reply,
            configuration=config,
            configuration_ready=True,
            discount_rate=rate,
        )

    def _apply_short_answer(
        self,
        text: str,
        request: Any,
        config: dict[str, Any],
        previous: Mapping[str, Any] | None,
    ) -> None:
        """Interpret a bare answer to the question asked in the last turn."""

        missing_before = missing_configuration_fields(previous or {})
        pending = missing_before[0] if missing_before else None

        stripped = text.strip()
        if pending == "customer_name" and not request.customer_name:
            if len(stripped) <= 60:
                config["customer_name"] = stripped.strip(" .,:;")
        elif pending == "region" and not request.region:
            if len(stripped) <= 40:
                config["region"] = stripped.strip(" .,:;").title()
        elif pending == "quantity" and not request.quantity:
            digits = "".join(
                character for character in stripped if character.isdigit()
            )
            if digits:
                config["quantity"] = int(digits)

        if config.get("discount_rate") is None:
            rate = parse_discount_rate(stripped)
            if rate is None and stripped.replace(".", "", 1).isdigit():
                value = float(stripped)
                if 0 < value <= 100:
                    rate = value / 100 if value >= 1 else value
            if rate is not None:
                config["discount_rate"] = rate

    def _configuration_summary(
        self, config: Mapping[str, Any], partial: bool = False
    ) -> str:
        lines = [
            "Here is what I have so far:"
            if partial
            else "The configuration is ready:"
        ]
        lines.append(f"- Customer: {config.get('customer_name') or '—'}")
        lines.append(f"- Region: {config.get('region') or '—'}")
        lines.append(f"- Currency: {config.get('currency') or '—'}")
        main_product = config.get("main_product")
        main_description = config.get("main_product_description") or ""
        lines.append(
            "- Main product: "
            + (f"{main_product} — {main_description}" if main_product else "—")
        )
        lines.append(f"- Quantity: {config.get('quantity') or '—'}")
        accessories = config.get("accessories") or []
        accessory_text = (
            ", ".join(item.get("description", "") for item in accessories)
            if accessories
            else "—"
        )
        lines.append(f"- Accessories: {accessory_text}")
        return "\n".join(lines)
