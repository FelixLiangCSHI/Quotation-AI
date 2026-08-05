"""Deterministic email composition plus optional Agent 3 wording assistance.

Composition is separated into three steps:

1. :func:`build_email_facts` reads trusted values from persisted domain state;
2. a deterministic template renders subject and body from those facts;
3. Agent 3 may optionally rewrite the wording, and any rewrite that drops or
   contradicts a protected value is discarded in favour of the template.

The deterministic template is both the default and the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from app.agents.agents import Agent3EmailWordingAgent, EmailWordingRequest
from app.config import CUSTOMER_PROHIBITED_FIELDS
from app.emailing.contracts import (
    CUSTOMER_FACING_TYPES,
    EmailAudience,
    EmailNotAllowedError,
    EmailType,
)

TEMPLATE_VERSION = "v1"

APPROVED_STATUSES = frozenset({"approved", "approved_with_override"})

#: Terms that must never appear in a customer-facing email body.
CUSTOMER_FORBIDDEN_TERMS = (
    "gross margin",
    "margin",
    "cost",
    "cogs",
    "threshold",
    "approval reason",
    "rule id",
    "internal comment",
    "override",
    "policy version",
)


@dataclass(frozen=True)
class EmailLineItem:
    product_id: str
    description: str
    quantity: int
    unit_price: str
    extended_price: str


@dataclass(frozen=True)
class EmailFacts:
    """The trusted values an email may state. All come from persisted state."""

    quotation_id: str
    quotation_version: int
    customer_name: str
    currency: str
    line_items: tuple[EmailLineItem, ...] = ()
    total_revenue: str = ""
    gross_margin_percent: str = ""
    threshold_percent: str = ""
    decision_status: str = ""
    approval_status: str = ""
    approver_name: str = ""
    approval_task_id: int | None = None
    task_reference: str = ""
    incoterm: str = ""
    quotation_date: str = ""
    validity_date: str = ""
    submitted_at: str = ""
    reminder_due_at: str = ""
    reason: str = ""
    triggered_rule_ids: tuple[str, ...] = ()

    def protected_values(self, *, audience: EmailAudience) -> tuple[str, ...]:
        """Values Agent 3 must preserve verbatim."""

        values: list[str] = [
            self.quotation_id,
            str(self.quotation_version),
            self.customer_name,
            self.currency,
        ]
        for item in self.line_items:
            values.extend(
                [
                    item.product_id,
                    item.description,
                    str(item.quantity),
                    item.unit_price,
                    item.extended_price,
                ]
            )
        values.append(self.total_revenue)
        values.append(self.incoterm)
        values.append(self.quotation_date)
        values.append(self.validity_date)
        if audience is EmailAudience.INTERNAL:
            values.extend(
                [
                    self.gross_margin_percent,
                    self.threshold_percent,
                    self.decision_status,
                    self.approval_status,
                    self.approver_name,
                    self.task_reference,
                    self.reminder_due_at,
                    self.reason,
                ]
            )
        return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True)
class ComposedEmail:
    """A rendered subject and body plus its composition provenance."""

    email_type: EmailType
    audience: EmailAudience
    subject: str
    body: str
    template_version: str = TEMPLATE_VERSION
    agent_provider: str = "deterministic"
    fallback_used: bool = True
    fallback_reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _decimal_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{Decimal(str(value)):.2f}"
    except (ArithmeticError, ValueError):
        return str(value)


def _iso(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def build_email_facts(
    *,
    quotation,
    state,
    task=None,
    approver_name: str = "",
    reason: str = "",
    reminder_due_at: datetime | None = None,
) -> EmailFacts:
    """Assemble trusted email facts from persisted domain state.

    ``quotation`` is a persisted :class:`~app.domain.dto.QuotationDTO` and
    ``state`` the decoded workflow state stored with it. Nothing here is
    derived from user input or from an AI response.
    """

    pricing = state.quotation_pricing
    decision = state.combined_decision
    currency = quotation.currency or state.draft.currency
    items: list[EmailLineItem] = []
    for item in state.draft.line_items:
        unit_price = _decimal_text(item.unit_price)
        extended = (
            _decimal_text(Decimal(str(item.unit_price)) * Decimal(item.quantity))
            if item.unit_price is not None
            else ""
        )
        items.append(
            EmailLineItem(
                product_id=item.product_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=unit_price,
                extended_price=extended,
            )
        )
    approval = state.approval
    return EmailFacts(
        quotation_id=quotation.quotation_id,
        quotation_version=quotation.version,
        customer_name=quotation.customer_name or state.draft.customer_name,
        currency=currency,
        line_items=tuple(items),
        total_revenue=_decimal_text(None if pricing is None else pricing.total_revenue),
        gross_margin_percent=_decimal_text(
            None if decision is None else decision.evaluated_margin_percent
        ),
        threshold_percent=_decimal_text(
            None if decision is None else decision.threshold_percent
        ),
        decision_status="" if decision is None else decision.status,
        approval_status=approval.status.value,
        approver_name=approver_name
        or (task.assigned_approver_name if task is not None else ""),
        approval_task_id=None if task is None else task.id,
        task_reference="" if task is None else task.task_reference,
        incoterm=quotation.incoterm or state.draft.incoterm,
        quotation_date=(
            state.draft.created_at.date().isoformat()
            if state.draft.created_at is not None
            else ""
        ),
        validity_date=(
            state.draft.validity_date.isoformat()
            if getattr(state.draft, "validity_date", None) is not None
            else ""
        ),
        submitted_at=_iso(None if task is None else task.submitted_at),
        reminder_due_at=_iso(
            reminder_due_at
            if reminder_due_at is not None
            else (None if task is None else task.reminder_due_at)
        ),
        reason=reason or approval.reason,
        triggered_rule_ids=(
            () if decision is None else tuple(decision.triggered_rule_ids)
        ),
    )


# -- deterministic templates ------------------------------------------------


def _line_item_block(facts: EmailFacts) -> str:
    if not facts.line_items:
        return "(no line items recorded)"
    return "\n".join(
        f"- {item.product_id} | {item.description} | qty {item.quantity} | "
        f"{facts.currency} {item.unit_price} | {facts.currency} "
        f"{item.extended_price}"
        for item in facts.line_items
    )


def render_approval_request(
    facts: EmailFacts, *, include_margin: bool
) -> tuple[str, str]:
    decision_line = {
        "pass": (
            "Decision: PASS. The quotation margin is above the configured "
            "policy threshold and requires your confirmation."
        ),
        "review_required": (
            "Decision: REVIEW_REQUIRED. The quotation margin is equal to or "
            "below the configured policy threshold and requires an override "
            "approval, a revision request, or a rejection."
        ),
    }.get(
        facts.decision_status,
        f"Decision: {facts.decision_status.upper() or 'UNKNOWN'}.",
    )
    margin_block = ""
    if include_margin:
        margin_block = (
            f"Gross margin: {facts.gross_margin_percent}%\n"
            f"Policy threshold: {facts.threshold_percent}%\n"
        )
    subject = (
        f"Approval requested: quotation {facts.quotation_id} "
        f"v{facts.quotation_version}"
    )
    body = (
        f"Hello {facts.approver_name or 'Approver'},\n\n"
        "A quotation has been submitted for your approval.\n\n"
        f"Quotation ID: {facts.quotation_id}\n"
        f"Quotation version: {facts.quotation_version}\n"
        f"Customer: {facts.customer_name}\n"
        f"Currency: {facts.currency}\n"
        f"Total revenue: {facts.currency} {facts.total_revenue}\n"
        f"Incoterm: {facts.incoterm}\n\n"
        f"Line items:\n{_line_item_block(facts)}\n\n"
        f"{decision_line}\n"
        f"{margin_block}"
        f"Approval task: {facts.task_reference}\n"
        f"Approval task id: {facts.approval_task_id}\n"
        f"Submitted at: {facts.submitted_at}\n"
        f"Reminder due at: {facts.reminder_due_at}\n\n"
        "Open the approval inbox to record your decision."
    )
    return subject, body


def render_reminder(facts: EmailFacts, *, include_margin: bool) -> tuple[str, str]:
    margin_block = ""
    if include_margin:
        margin_block = (
            f"Gross margin: {facts.gross_margin_percent}%\n"
            f"Policy threshold: {facts.threshold_percent}%\n"
        )
    subject = (
        f"Reminder: quotation {facts.quotation_id} v{facts.quotation_version} "
        "is still awaiting approval"
    )
    body = (
        f"Hello {facts.approver_name or 'Approver'},\n\n"
        "This approval task is still pending.\n\n"
        f"Quotation ID: {facts.quotation_id}\n"
        f"Quotation version: {facts.quotation_version}\n"
        f"Customer: {facts.customer_name}\n"
        f"Original decision: {facts.decision_status}\n"
        f"{margin_block}"
        f"Approval task: {facts.task_reference}\n"
        f"Approval task id: {facts.approval_task_id}\n"
        f"Submitted at: {facts.submitted_at}\n"
        f"Reminder due at: {facts.reminder_due_at}\n\n"
        "Open the approval inbox to record your decision."
    )
    return subject, body


def render_customer_quotation(facts: EmailFacts) -> tuple[str, str]:
    subject = f"Quotation {facts.quotation_id} for {facts.customer_name}"
    body = (
        f"Dear {facts.customer_name},\n\n"
        "Thank you for your enquiry. Please find our quotation below.\n\n"
        f"Quotation reference: {facts.quotation_id}\n"
        f"Quotation version: {facts.quotation_version}\n"
        f"Currency: {facts.currency}\n\n"
        f"Items:\n{_line_item_block(facts)}\n\n"
        f"Total: {facts.currency} {facts.total_revenue}\n"
        f"Incoterm: {facts.incoterm}\n"
        f"Quotation date: {facts.quotation_date}\n\n"
        "Please contact your sales representative if you would like to "
        "discuss the proposed configuration.\n\n"
        "Kind regards,\nQuotation Team"
    )
    return subject, body


def render_revision_request(facts: EmailFacts, *, include_rules: bool) -> tuple[str, str]:
    rules = (
        f"Referenced rules: {', '.join(facts.triggered_rule_ids)}\n"
        if include_rules and facts.triggered_rule_ids
        else ""
    )
    subject = (
        f"Revision requested: quotation {facts.quotation_id} "
        f"v{facts.quotation_version}"
    )
    body = (
        "Hello,\n\n"
        "A revision has been requested for the quotation below.\n\n"
        f"Quotation ID: {facts.quotation_id}\n"
        f"Quotation version: {facts.quotation_version}\n"
        f"Customer: {facts.customer_name}\n"
        f"Approval status: {facts.approval_status}\n"
        f"Requested by: {facts.approver_name}\n"
        f"Revision reason: {facts.reason}\n"
        f"{rules}\n"
        "Please update the quotation, rerun pricing and validation, and "
        "resubmit it for approval."
    )
    return subject, body


def render_rejection(facts: EmailFacts, *, include_rules: bool) -> tuple[str, str]:
    rules = (
        f"Referenced rules: {', '.join(facts.triggered_rule_ids)}\n"
        if include_rules and facts.triggered_rule_ids
        else ""
    )
    subject = (
        f"Quotation {facts.quotation_id} v{facts.quotation_version} rejected"
    )
    body = (
        "Hello,\n\n"
        "The quotation below was rejected. This message is internal and must "
        "not be forwarded to the customer.\n\n"
        f"Quotation ID: {facts.quotation_id}\n"
        f"Quotation version: {facts.quotation_version}\n"
        f"Customer: {facts.customer_name}\n"
        f"Approval status: {facts.approval_status}\n"
        f"Rejected by: {facts.approver_name}\n"
        f"Rejection reason: {facts.reason}\n"
        f"{rules}"
    )
    return subject, body


DETERMINISTIC_RENDERERS = {
    EmailType.APPROVAL_REQUEST: render_approval_request,
    EmailType.APPROVAL_REMINDER: render_reminder,
    EmailType.CUSTOMER_QUOTATION: render_customer_quotation,
    EmailType.REVISION_REQUEST: render_revision_request,
    EmailType.REJECTION_NOTIFICATION: render_rejection,
}


# -- Agent 3 boundary -------------------------------------------------------


def customer_boundary_problems(text: str) -> tuple[str, ...]:
    """Return the internal terms found in customer-facing text."""

    lowered = text.casefold()
    problems = [term for term in CUSTOMER_FORBIDDEN_TERMS if term in lowered]
    problems.extend(
        name
        for name in sorted(CUSTOMER_PROHIBITED_FIELDS)
        if name.replace("_", " ") in lowered
    )
    return tuple(dict.fromkeys(problems))


def compose_email(
    *,
    email_type: EmailType,
    audience: EmailAudience,
    facts: EmailFacts,
    include_margin: bool = False,
    include_rules: bool = False,
    agent: Agent3EmailWordingAgent | None = None,
    template_version: str = TEMPLATE_VERSION,
) -> ComposedEmail:
    """Render the deterministic template and optionally let Agent 3 reword it.

    The AI output is used only when it validates: correct schema, every
    protected value preserved, and no customer/internal boundary violation.
    """

    renderer = DETERMINISTIC_RENDERERS[email_type]
    if email_type in {EmailType.APPROVAL_REQUEST, EmailType.APPROVAL_REMINDER}:
        subject, body = renderer(facts, include_margin=include_margin)
    elif email_type in {
        EmailType.REVISION_REQUEST,
        EmailType.REJECTION_NOTIFICATION,
    }:
        subject, body = renderer(facts, include_rules=include_rules)
    else:
        subject, body = renderer(facts)

    deterministic = ComposedEmail(
        email_type=email_type,
        audience=audience,
        subject=subject,
        body=body,
        template_version=template_version,
    )
    if audience is EmailAudience.CUSTOMER:
        leaked = customer_boundary_problems(f"{subject}\n{body}")
        if leaked:
            raise EmailNotAllowedError(
                "The deterministic customer template leaked internal terms: "
                + ", ".join(leaked)
            )
    if agent is None:
        return deterministic

    # Agent 3 must preserve every trusted value the template actually stated.
    # A value the template omitted (a margin hidden from this recipient, for
    # example) must not become something the AI is asked to reproduce.
    rendered = f"{subject}\n{body}"
    protected = tuple(
        value
        for value in facts.protected_values(audience=audience)
        if value in rendered
    )
    outcome = agent.run(
        EmailWordingRequest(
            email_type=email_type.value,
            subject=subject,
            body=body,
            protected_values=protected,
        )
    )
    if outcome.fallback_used:
        return ComposedEmail(
            email_type=email_type,
            audience=audience,
            subject=subject,
            body=body,
            template_version=template_version,
            agent_provider=outcome.audit.provider,
            fallback_used=True,
            fallback_reason=outcome.audit.error_category.value,
            metadata={"agent_error_detail": outcome.audit.error_detail},
        )

    candidate = outcome.value
    combined = f"{candidate.subject}\n{candidate.body}"
    if audience is EmailAudience.CUSTOMER:
        leaked = customer_boundary_problems(combined)
        if leaked:
            return ComposedEmail(
                email_type=email_type,
                audience=audience,
                subject=subject,
                body=body,
                template_version=template_version,
                agent_provider=outcome.audit.provider,
                fallback_used=True,
                fallback_reason="customer_boundary",
                metadata={"leaked_terms": list(leaked)},
            )
    return ComposedEmail(
        email_type=email_type,
        audience=audience,
        subject=candidate.subject,
        body=candidate.body,
        template_version=template_version,
        agent_provider=outcome.audit.provider,
        fallback_used=False,
    )


def require_customer_approval(facts: EmailFacts) -> None:
    """Refuse to compose a customer email before an approval decision."""

    if facts.approval_status not in APPROVED_STATUSES:
        raise EmailNotAllowedError(
            "A customer quotation email requires an approved quotation "
            f"(current approval status: {facts.approval_status or 'none'})."
        )


def audience_for(email_type: EmailType) -> EmailAudience:
    return (
        EmailAudience.CUSTOMER
        if email_type in CUSTOMER_FACING_TYPES
        else EmailAudience.INTERNAL
    )
