from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from app.agents import Agent1RequirementAgent, RequirementRequest
from app.config import REQUIRED_QUOTATION_FIELDS
from app.natural_language import QuoteRequest, parse_quote_request
from app.quotation_models import QuotationDraft, WorkflowStage, utc_now
from app.recommender import QuoteRecommendation, QuoteRecommender
from app.requirement_fields import (
    AGENT_EXTRACTABLE_FIELDS,
    REQUIREMENT_FIELD_SPECS,
    field_question,
)
from app.requirement_intake import (
    PendingConfirmation,
    RejectedCandidate,
    RequirementCandidate,
    candidates_from_mapping,
    merge_candidates,
)


FIELD_QUESTIONS = {
    name: spec.question for name, spec in REQUIREMENT_FIELD_SPECS.items()
}

CORRECTION_RE = re.compile(
    r"\b(?:actually|change|correct|instead|should\s+be|update)\b|"
    r"(?:改为|更改|修改|其实)",
    re.IGNORECASE,
)

FIELD_CORRECTION_CUES = {
    "customer_name": re.compile(r"\bcustomer\b|客户", re.IGNORECASE),
    "region": re.compile(r"\b(?:region|market)\b|区域|市场", re.IGNORECASE),
    "product_query": re.compile(
        r"\b(?:product|system|model)\b|产品|系统|型号",
        re.IGNORECASE,
    ),
    "selected_product_ids": re.compile(
        r"\b(?:product|model|select)\b|产品|型号|选择",
        re.IGNORECASE,
    ),
    "quantity": re.compile(r"\b(?:qty|quantity|units?)\b|数量|台", re.IGNORECASE),
    "currency": re.compile(
        r"\b(?:currency|USD|SGD|RMB|CNY|EUR)\b|币种|货币",
        re.IGNORECASE,
    ),
    "incoterm": re.compile(
        r"\b(?:incoterm|EXW|FCA|FOB|CIF|DAP|DDP)\b",
        re.IGNORECASE,
    ),
    "delivery_location": re.compile(
        r"\b(?:deliver|delivery|location)\b|交付|地点",
        re.IGNORECASE,
    ),
    "target_price": re.compile(r"\b(?:target|price)\b|目标价", re.IGNORECASE),
}


@dataclass(frozen=True)
class ConversationTurnResult:
    updated_draft: QuotationDraft
    extracted_fields: dict[str, Any]
    changed_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    next_question: str | None
    notices: tuple[str, ...]
    ready_for_analysis: bool
    product_recommendation: QuoteRecommendation | None = None
    rejected_candidates: tuple[RejectedCandidate, ...] = ()
    pending_confirmations: tuple[PendingConfirmation, ...] = ()
    agent_fallback_used: bool = True


class RequirementConversationAgent:
    def __init__(
        self,
        recommender: QuoteRecommender | None = None,
        required_fields: Iterable[str] = REQUIRED_QUOTATION_FIELDS,
        requirement_agent: Agent1RequirementAgent | None = None,
    ) -> None:
        self.recommender = recommender or QuoteRecommender()
        self.requirement_agent = requirement_agent
        self.required_fields = tuple(required_fields)
        unsupported_fields = set(self.required_fields).difference(FIELD_QUESTIONS)
        if unsupported_fields:
            fields = ", ".join(sorted(unsupported_fields))
            raise ValueError(f"Unsupported required fields: {fields}")

    def process_message(
        self,
        message: str,
        current_draft: QuotationDraft,
    ) -> ConversationTurnResult:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be blank")

        updated_draft = deepcopy(current_draft)
        request = parse_quote_request(normalized_message)
        extracted_fields = self._extracted_fields(request)
        self._add_contextual_answer(
            extracted_fields,
            normalized_message,
            current_draft.missing_fields,
        )

        changed_fields: list[str] = []
        notices: list[str] = []
        rejected: list[RejectedCandidate] = []
        has_correction_marker = bool(CORRECTION_RE.search(normalized_message))
        correction_fields: list[str] = []
        for field_name, value in list(extracted_fields.items()):
            try:
                value = REQUIREMENT_FIELD_SPECS[field_name].validate(value)
            except Exception as error:  # noqa: BLE001 - never corrupt the draft
                rejected.append(
                    RejectedCandidate(
                        field_name=field_name,
                        value=value,
                        reason=str(error),
                        source="deterministic",
                    )
                )
                extracted_fields.pop(field_name, None)
                continue
            extracted_fields[field_name] = value
            current_value = getattr(updated_draft, field_name)
            is_field_correction = self._is_field_correction(
                field_name,
                normalized_message,
                extracted_fields,
                has_correction_marker,
            )
            if is_field_correction:
                correction_fields.append(field_name)
            if field_name == "selected_product_ids":
                can_replace = not current_value or is_field_correction
            else:
                can_replace = (
                    _is_empty(current_value)
                    or field_name in current_draft.missing_fields
                    or is_field_correction
                )

            if not can_replace and current_value != value:
                if has_correction_marker:
                    continue
                notices.append(
                    f"I kept the existing {field_name.replace('_', ' ')}. "
                    f"Say 'change {field_name.replace('_', ' ')} to ...' to correct it."
                )
                continue
            if current_value != value:
                setattr(updated_draft, field_name, deepcopy(value))
                changed_fields.append(field_name)

        if (
            {"product_query", "region"}.intersection(changed_fields)
            and updated_draft.selected_product_ids
            and "selected_product_ids" not in extracted_fields
        ):
            updated_draft.selected_product_ids = []
            changed_fields.append("selected_product_ids")
            notices.append(
                "The product selection was cleared because the product request or region changed."
            )

        agent_outcome = self._run_requirement_agent(
            normalized_message,
            updated_draft,
        )
        pending: tuple[PendingConfirmation, ...] = ()
        agent_fallback_used = True
        if agent_outcome is not None:
            agent_fallback_used = agent_outcome.fallback_used
            agent_candidates = self._agent_candidates(
                agent_outcome.value,
                skip_fields=set(extracted_fields),
            )
            merge = merge_candidates(
                updated_draft,
                agent_candidates,
                correction_fields=correction_fields,
            )
            updated_draft = merge.draft
            changed_fields.extend(merge.changed_fields)
            rejected.extend(merge.rejected)
            pending = merge.pending
            notices.extend(merge.notices)

        missing_fields = self._calculate_missing_fields(
            updated_draft,
            previous_missing=current_draft.missing_fields,
            extracted_fields={
                **extracted_fields,
                **{name: True for name in changed_fields},
            },
        )
        updated_draft.missing_fields = list(missing_fields)
        updated_draft.updated_at = utc_now()

        recommendation = self._recommend(updated_draft)
        ready_for_analysis = not missing_fields and bool(
            updated_draft.selected_product_ids
        )
        if ready_for_analysis:
            updated_draft.status = WorkflowStage.READY_FOR_ANALYSIS
            next_question = None
        else:
            updated_draft.status = WorkflowStage.COLLECTING_REQUIREMENTS
            next_question = self._next_question(missing_fields, recommendation)
        if pending:
            next_question = pending[0].question

        return ConversationTurnResult(
            updated_draft=updated_draft,
            extracted_fields=extracted_fields,
            changed_fields=tuple(changed_fields),
            missing_fields=missing_fields,
            next_question=next_question,
            notices=tuple(notices),
            ready_for_analysis=ready_for_analysis,
            product_recommendation=recommendation,
            rejected_candidates=tuple(rejected),
            pending_confirmations=pending,
            agent_fallback_used=agent_fallback_used,
        )

    def _run_requirement_agent(
        self,
        message: str,
        draft: QuotationDraft,
    ):
        """Call Agent 1 when configured. Agent 1 is always optional."""

        if self.requirement_agent is None:
            return None
        known_fields = {
            name: str(getattr(draft, name))
            for name in AGENT_EXTRACTABLE_FIELDS
            if not _is_empty(getattr(draft, name, None))
        }
        request = RequirementRequest(
            customer_request=message,
            known_fields=known_fields,
            missing_fields=tuple(draft.missing_fields),
            candidate_products=tuple(draft.selected_product_ids),
        )
        try:
            return self.requirement_agent.run(request)
        except Exception:  # noqa: BLE001 - Agent 1 must never block the flow
            return None

    @staticmethod
    def _agent_candidates(
        response,
        *,
        skip_fields: set[str],
    ) -> tuple[RequirementCandidate, ...]:
        """Convert an Agent 1 response into validated candidate values.

        Fields outside :data:`AGENT_EXTRACTABLE_FIELDS` are dropped here, so
        the provider cannot propose a price, a rule outcome or an approval.
        """

        candidates: list[RequirementCandidate] = []
        for item in getattr(response, "requirements", ()):  # pragma: no branch
            field_name = str(item.field_name).strip()
            if field_name not in AGENT_EXTRACTABLE_FIELDS:
                continue
            if field_name in skip_fields:
                continue
            candidates.append(
                RequirementCandidate(
                    field_name=field_name,
                    value=item.value,
                    confidence=item.confidence,
                    source="agent1",
                )
            )
        return tuple(candidates)

    def apply_structured_form(
        self,
        draft: QuotationDraft,
        values: dict[str, Any],
    ):
        """Apply a structured form submission to the same domain model.

        Form entries are explicit user input, so they replace existing values
        and are never parked for confirmation. They still pass through the
        same validation and merge logic as conversational input.
        """

        outcome = merge_candidates(
            draft,
            candidates_from_mapping(values, confidence=1.0, source="form"),
            force_replace=True,
        )
        updated_draft = outcome.draft
        if (
            {"product_query", "region"}.intersection(outcome.changed_fields)
            and updated_draft.selected_product_ids
            and "selected_product_ids" not in outcome.applied
        ):
            updated_draft.selected_product_ids = []
            outcome.changed_fields = (
                *outcome.changed_fields,
                "selected_product_ids",
            )
        updated_draft.missing_fields = list(
            self._calculate_missing_fields(
                updated_draft,
                previous_missing=[],
                extracted_fields=outcome.applied,
            )
        )
        updated_draft.status = (
            WorkflowStage.READY_FOR_ANALYSIS
            if not updated_draft.missing_fields
            and updated_draft.selected_product_ids
            else WorkflowStage.COLLECTING_REQUIREMENTS
        )
        updated_draft.updated_at = utc_now()
        return outcome

    @staticmethod
    def _is_field_correction(
        field_name: str,
        message: str,
        extracted_fields: dict[str, Any],
        has_correction_marker: bool,
    ) -> bool:
        if not has_correction_marker:
            return False
        if len(extracted_fields) == 1:
            return True
        cue = FIELD_CORRECTION_CUES.get(field_name)
        return bool(cue and cue.search(message))

    def select_product(
        self,
        draft: QuotationDraft,
        product_id: str,
        recommendation: QuoteRecommendation,
    ) -> QuotationDraft:
        normalized_product_id = product_id.strip()
        selectable_ids = {
            item.product_id
            for item in (
                recommendation.main_model,
                *recommendation.alternatives,
            )
            if item is not None
        }
        if normalized_product_id not in selectable_ids:
            raise ValueError("Selected product is not in the current recommendation")

        updated_draft = deepcopy(draft)
        updated_draft.selected_product_ids = [normalized_product_id]
        updated_draft.missing_fields = list(
            self._calculate_missing_fields(
                updated_draft,
                previous_missing=updated_draft.missing_fields,
                extracted_fields={"selected_product_ids": [normalized_product_id]},
            )
        )
        updated_draft.updated_at = utc_now()
        updated_draft.status = (
            WorkflowStage.READY_FOR_ANALYSIS
            if not updated_draft.missing_fields
            else WorkflowStage.COLLECTING_REQUIREMENTS
        )
        return updated_draft

    @staticmethod
    def _extracted_fields(request: QuoteRequest) -> dict[str, Any]:
        fields = {
            "customer_name": request.customer_name,
            "region": request.region,
            "product_query": request.product_query,
            "quantity": request.quantity,
            "currency": request.currency,
            "incoterm": request.incoterm,
            "delivery_location": request.delivery_location,
            "target_price": request.target_price,
        }
        extracted = {
            field_name: value
            for field_name, value in fields.items()
            if not _is_empty(value)
        }
        if request.product_ids:
            extracted["selected_product_ids"] = list(request.product_ids)
        return extracted

    def _add_contextual_answer(
        self,
        extracted_fields: dict[str, Any],
        message: str,
        previous_missing: list[str],
    ) -> None:
        next_missing = next(
            (field for field in self.required_fields if field in previous_missing),
            None,
        )
        if next_missing is None or next_missing in extracted_fields:
            return

        clean_message = message.strip(" \t,.;:!?，。；：")
        if next_missing == "customer_name":
            extracted_fields[next_missing] = clean_message
        elif next_missing == "region":
            extracted_fields[next_missing] = clean_message.casefold()
        elif next_missing == "product_query":
            extracted_fields[next_missing] = clean_message
        elif next_missing == "quantity" and clean_message.isdigit():
            quantity = int(clean_message)
            if quantity > 0:
                extracted_fields[next_missing] = quantity
        elif next_missing == "delivery_location":
            extracted_fields[next_missing] = clean_message

    def _calculate_missing_fields(
        self,
        draft: QuotationDraft,
        *,
        previous_missing: list[str],
        extracted_fields: dict[str, Any],
    ) -> tuple[str, ...]:
        missing: list[str] = []
        for field_name in self.required_fields:
            if field_name == "product_query":
                has_value = bool(
                    draft.product_query.strip() or draft.selected_product_ids
                )
            else:
                has_value = not _is_empty(getattr(draft, field_name))

            was_unconfirmed_default = (
                field_name in previous_missing
                and field_name not in extracted_fields
                and field_name not in {"product_query"}
            )
            if not has_value or was_unconfirmed_default:
                missing.append(field_name)
        return tuple(missing)

    def _recommend(self, draft: QuotationDraft) -> QuoteRecommendation | None:
        if not draft.product_query.strip() and not draft.selected_product_ids:
            return None
        query_parts = [draft.product_query.strip()]
        query_parts.extend(draft.selected_product_ids)
        if draft.region:
            query_parts.append(f"for {draft.region}")
        return self.recommender.recommend_from_text(
            " ".join(part for part in query_parts if part)
        )

    @staticmethod
    def _next_question(
        missing_fields: tuple[str, ...],
        recommendation: QuoteRecommendation | None,
    ) -> str | None:
        if missing_fields:
            return field_question(missing_fields[0])
        if recommendation and recommendation.main_model:
            return "Please select a recommended product before pricing analysis."
        return "Please provide enough product detail for a catalog recommendation."


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    if isinstance(value, (int, float)):
        return value <= 0
    return False
