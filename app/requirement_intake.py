"""Deterministic merge of requirement candidate values.

Candidates arrive from three sources: the deterministic parser, the optional
Agent 1 provider, and the structured form. All three go through the same
merge logic here, so conversational and form entry always update the
quotation domain model identically.

Merge rules, in order:

1. The candidate must validate against :mod:`app.requirement_fields`.
   Anything that fails is rejected and recorded; it never reaches the draft.
2. A low-confidence candidate is never written silently. It is parked as a
   pending confirmation until the user confirms or discards it.
3. An empty field is filled. A field that already holds a value is only
   replaced when the user explicitly corrects it, or when the caller is the
   structured form (an explicit user edit).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.quotation_models import QuotationDraft, utc_now
from app.requirement_fields import (
    CONFIDENCE_CONFIRMATION_THRESHOLD,
    RequirementValidationError,
    field_label,
    field_spec,
    validate_field,
)


@dataclass(frozen=True)
class RequirementCandidate:
    """A proposed value for one requirement field."""

    field_name: str
    value: Any
    confidence: float = 1.0
    source: str = "deterministic"


@dataclass(frozen=True)
class RejectedCandidate:
    field_name: str
    value: Any
    reason: str
    source: str


@dataclass(frozen=True)
class PendingConfirmation:
    """A validated but low-confidence candidate awaiting explicit approval."""

    field_name: str
    value: Any
    confidence: float
    source: str

    @property
    def question(self) -> str:
        return (
            f"I am not confident about the {field_label(self.field_name)}. "
            f"Should I record '{_display(self.value)}'?"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PendingConfirmation":
        return cls(
            field_name=str(payload["field_name"]),
            value=payload.get("value"),
            confidence=float(payload.get("confidence", 0.0)),
            source=str(payload.get("source", "unknown")),
        )


@dataclass
class MergeOutcome:
    draft: QuotationDraft
    changed_fields: tuple[str, ...] = ()
    applied: dict[str, Any] = field(default_factory=dict)
    rejected: tuple[RejectedCandidate, ...] = ()
    pending: tuple[PendingConfirmation, ...] = ()
    notices: tuple[str, ...] = ()


def merge_candidates(
    draft: QuotationDraft,
    candidates: Iterable[RequirementCandidate],
    *,
    correction_fields: Sequence[str] = (),
    force_replace: bool = False,
    confidence_threshold: float = CONFIDENCE_CONFIRMATION_THRESHOLD,
) -> MergeOutcome:
    """Merge ``candidates`` into a copy of ``draft`` deterministically."""

    updated = deepcopy(draft)
    corrections = set(correction_fields)
    changed: list[str] = []
    applied: dict[str, Any] = {}
    rejected: list[RejectedCandidate] = []
    pending: list[PendingConfirmation] = []
    notices: list[str] = []

    for candidate in candidates:
        try:
            spec = field_spec(candidate.field_name)
            value = spec.validate(candidate.value)
        except RequirementValidationError as error:
            rejected.append(
                RejectedCandidate(
                    field_name=candidate.field_name,
                    value=candidate.value,
                    reason=str(error),
                    source=candidate.source,
                )
            )
            continue

        confidence = _clamp_confidence(candidate.confidence)
        if confidence < confidence_threshold:
            pending.append(
                PendingConfirmation(
                    field_name=candidate.field_name,
                    value=value,
                    confidence=confidence,
                    source=candidate.source,
                )
            )
            continue

        current = getattr(updated, candidate.field_name)
        if current == value:
            continue

        can_replace = (
            force_replace
            or _is_empty(current)
            or candidate.field_name in updated.missing_fields
            or candidate.field_name in corrections
        )
        if not can_replace:
            notices.append(
                f"I kept the existing {field_label(candidate.field_name)}. "
                f"Say 'change {field_label(candidate.field_name)} to ...' "
                "to correct it."
            )
            continue

        setattr(updated, candidate.field_name, deepcopy(value))
        changed.append(candidate.field_name)
        applied[candidate.field_name] = value

    if pending:
        updated.pending_confirmations = _merge_pending(
            updated.pending_confirmations, pending
        )
    if changed or pending:
        updated.updated_at = utc_now()

    return MergeOutcome(
        draft=updated,
        changed_fields=tuple(changed),
        applied=applied,
        rejected=tuple(rejected),
        pending=tuple(pending),
        notices=tuple(dict.fromkeys(notices)),
    )


def confirm_pending(
    draft: QuotationDraft,
    field_name: str,
    *,
    accept: bool = True,
) -> MergeOutcome:
    """Apply or discard a parked low-confidence candidate."""

    remaining = [
        item
        for item in draft.pending_confirmations
        if item.get("field_name") != field_name
    ]
    matches = [
        PendingConfirmation.from_dict(item)
        for item in draft.pending_confirmations
        if item.get("field_name") == field_name
    ]
    if not matches:
        raise RequirementValidationError(
            f"No pending confirmation for {field_name}."
        )

    if not accept:
        updated = deepcopy(draft)
        updated.pending_confirmations = remaining
        updated.updated_at = utc_now()
        return MergeOutcome(draft=updated, notices=("Suggestion discarded.",))

    confirmed = matches[-1]
    outcome = merge_candidates(
        draft,
        [
            RequirementCandidate(
                field_name=confirmed.field_name,
                value=confirmed.value,
                confidence=1.0,
                source=f"{confirmed.source}:confirmed",
            )
        ],
        force_replace=True,
    )
    outcome.draft.pending_confirmations = remaining
    return outcome


def pending_confirmations(draft: QuotationDraft) -> tuple[PendingConfirmation, ...]:
    return tuple(
        PendingConfirmation.from_dict(item)
        for item in draft.pending_confirmations
    )


def candidates_from_mapping(
    values: dict[str, Any],
    *,
    confidence: float = 1.0,
    source: str = "form",
) -> tuple[RequirementCandidate, ...]:
    return tuple(
        RequirementCandidate(
            field_name=name,
            value=value,
            confidence=confidence,
            source=source,
        )
        for name, value in values.items()
        if not _is_omitted(value)
    )


def _is_omitted(value: Any) -> bool:
    """A form field the user left blank. A zero or negative number is not
    omitted: it is an invalid entry and must be reported as rejected."""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _merge_pending(
    existing: list[dict[str, Any]],
    new_items: Sequence[PendingConfirmation],
) -> list[dict[str, Any]]:
    new_fields = {item.field_name for item in new_items}
    kept = [
        item for item in existing if item.get("field_name") not in new_fields
    ]
    return [*kept, *(item.to_dict() for item in new_items)]


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(confidence, 0.0), 1.0)


def _display(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value <= 0
    return False


__all__ = [
    "MergeOutcome",
    "PendingConfirmation",
    "RejectedCandidate",
    "RequirementCandidate",
    "candidates_from_mapping",
    "confirm_pending",
    "merge_candidates",
    "pending_confirmations",
    "validate_field",
]
