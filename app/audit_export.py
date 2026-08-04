from __future__ import annotations

import json
from typing import Any

from app.quotation_models import AuditEvent, QuotationWorkflowState


def build_internal_audit_export(
    state: QuotationWorkflowState,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "export_type": "internal_audit",
        "quotation": _draft_summary(state),
        "selected_products": list(state.draft.selected_product_ids),
        "pricing_summary": _internal_pricing_summary(state),
        "validation_summary": _internal_validation_summary(state),
        "approval_record": _approval_summary(state, internal=True),
        "audit_events": [_internal_event(event) for event in state.audit_events],
    }


def build_customer_quotation_export(
    state: QuotationWorkflowState,
) -> dict[str, Any]:
    current_events = [
        event
        for event in state.audit_events
        if event.quotation_id == state.draft.quotation_id
    ]
    return {
        "schema_version": "1.0",
        "export_type": "customer_quotation_data",
        "quotation": _draft_summary(state),
        "selected_products": list(state.draft.selected_product_ids),
        "pricing_summary": _customer_pricing_summary(state),
        "validation_summary": _customer_validation_summary(state),
        "approval_record": _approval_summary(state, internal=False),
        "audit_events": [_customer_event(event) for event in current_events],
    }


def export_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=_json_default,
    ).encode("utf-8")


def _draft_summary(state: QuotationWorkflowState) -> dict[str, Any]:
    draft = state.draft
    return {
        "quotation_id": draft.quotation_id,
        "customer_name": draft.customer_name,
        "customer_type": draft.customer_type,
        "region": draft.region,
        "product_query": draft.product_query,
        "quantity": draft.quantity,
        "currency": draft.currency,
        "incoterm": draft.incoterm,
        "delivery_location": draft.delivery_location,
        "requested_delivery_date": draft.requested_delivery_date,
        "target_price": draft.target_price,
        "proposed_unit_price": draft.proposed_unit_price,
        "status": draft.status.value,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _internal_pricing_summary(
    state: QuotationWorkflowState,
) -> dict[str, Any] | None:
    pricing = state.pricing_result
    if pricing is None:
        return None
    return {
        "currency": pricing.currency,
        "recommended_unit_price": pricing.recommended_unit_price,
        "total_price": pricing.total_price,
        "reference_list_price": pricing.reference_list_price,
        "reference_net_price": pricing.reference_net_price,
        "estimated_cost": pricing.estimated_cost,
        "gross_margin_amount": pricing.gross_margin_amount,
        "gross_margin_percent": pricing.gross_margin_percent,
        "discount_percent": pricing.discount_percent,
        "comparable_count": pricing.comparable_count,
        "confidence_score": pricing.confidence_score,
        "confidence_label": pricing.confidence_label,
        "cost_basis_complete": pricing.cost_basis_complete,
        "assumptions": list(pricing.assumptions),
        "warnings": list(pricing.warnings),
    }


def _customer_pricing_summary(
    state: QuotationWorkflowState,
) -> dict[str, Any] | None:
    pricing = state.pricing_result
    if pricing is None:
        return None
    return {
        "currency": pricing.currency,
        "recommended_unit_price": pricing.recommended_unit_price,
        "total_price": pricing.total_price,
        "reference_list_price": pricing.reference_list_price,
        "discount_percent": pricing.discount_percent,
        "confidence_label": pricing.confidence_label,
        "warnings": list(pricing.warnings),
    }


def _internal_validation_summary(
    state: QuotationWorkflowState,
) -> dict[str, Any]:
    technical = state.technical_validation
    commercial = state.commercial_validation
    decision = state.combined_decision
    return {
        "stale": state.validation_stale,
        "technical": (
            {
                "status": technical.status,
                "passed_checks": list(technical.passed_checks),
                "warnings": list(technical.warnings),
                "errors": list(technical.errors),
                "not_evaluated_checks": list(
                    technical.not_evaluated_checks
                ),
                "checked_rules": list(technical.checked_rules),
            }
            if technical
            else None
        ),
        "commercial": (
            {
                "status": commercial.status,
                "approval_required": commercial.approval_required,
                "rules": [
                    {
                        "rule_id": rule.rule_id,
                        "name": rule.name,
                        "status": rule.status,
                        "message": rule.message,
                    }
                    for rule in commercial.rule_results
                ],
            }
            if commercial
            else None
        ),
        "decision": (
            {
                "status": decision.status,
                "summary": decision.summary,
                "triggered_rule_ids": list(decision.triggered_rule_ids),
                "approval_required": decision.approval_required,
                "recommended_next_action": decision.recommended_next_action,
            }
            if decision
            else None
        ),
    }


def _customer_validation_summary(
    state: QuotationWorkflowState,
) -> dict[str, Any]:
    technical = state.technical_validation
    decision = state.combined_decision
    return {
        "stale": state.validation_stale,
        "technical": (
            {
                "status": technical.status,
                "warnings": list(technical.warnings),
                "errors": list(technical.errors),
            }
            if technical
            else None
        ),
        "decision": (
            {
                "status": decision.status,
                "summary": decision.summary,
            }
            if decision
            else None
        ),
    }


def _approval_summary(
    state: QuotationWorkflowState,
    *,
    internal: bool,
) -> dict[str, Any]:
    approval = state.approval
    summary = {
        "status": approval.status.value,
        "final_approved_price": approval.final_price,
        "timestamp": approval.timestamp,
    }
    if internal:
        summary.update(
            {
                "actor": approval.actor,
                "actor_role": approval.actor_role,
                "action": approval.action,
                "reason": approval.reason,
                "original_recommended_price": approval.original_price,
                "triggered_rule_ids": list(approval.triggered_rule_ids),
                "override_justification": approval.override_justification,
                "reminder_due_at": approval.reminder_due_at,
            }
        )
    return summary


def _internal_event(event: AuditEvent) -> dict[str, Any]:
    return {
        "quotation_id": event.quotation_id,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "actor": event.actor,
        "before_state": event.before_state,
        "after_state": event.after_state,
        "changed_fields": list(event.changed_fields),
        "reason": event.reason,
        "triggered_rule_ids": list(event.triggered_rule_ids),
    }


def _customer_event(event: AuditEvent) -> dict[str, Any]:
    return {
        "quotation_id": event.quotation_id,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "before_state": event.before_state,
        "after_state": event.after_state,
        "changed_fields": list(event.changed_fields),
    }


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return str(value.value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")
