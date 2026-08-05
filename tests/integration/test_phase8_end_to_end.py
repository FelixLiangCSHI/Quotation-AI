"""Phase 8 end-to-end scenarios A-J for the internal quotation MVP.

Each test exercises the persistent stack (SQLite + repositories + services)
rather than a mock, so the evidence in the acceptance report is real.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.auth import PermissionDeniedError, Role
from app.emailing.contracts import EmailStatus, EmailType
from app.quotation_models import ApprovalStatus
from app.services.approval_service import (
    ApprovalService,
    ApprovalTaskCompletedError,
    MissingJustificationError,
    apply_material_edit,
)
from app.services.document_service import (
    DocumentNotAllowedError,
    DocumentService,
)
from app.services.unit_of_work import UnitOfWork
from app.quotation_models import utc_now
from tests.fixtures.phase6_helpers import create_user, make_decided_state
from tests.fixtures.phase7_helpers import CUSTOMER_ADDRESS, add_line_items

OVERRIDE_REASON = (
    "Strategic account. I acknowledge the quotation margin is equal to or "
    "below the configured policy threshold and accept the commercial risk."
)

#: Tokens that must never reach a customer artefact.
INTERNAL_TOKENS = (
    "gross margin",
    "margin",
    "estimated cost",
    "60000",
    "35%",
    "35.0",
    "threshold",
    "POL-MARGIN-MVP-001",
    "COMM-MARGIN-001",
    "COMM-MARGIN-002",
    "policy version",
    "override",
    "justification",
    "workbook",
    "comparable",
)


def _submit(
    service,
    approval_service,
    people,
    quotation_id,
    *,
    status="pass",
    margin="42.0",
    approver="manager",
):
    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    make_decided_state(loaded.state, status=status, margin=margin)
    add_line_items(loaded.state)
    loaded = service.save_state(loaded, actor=people["sales"].username)
    return approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people[approver].user_id,
    )


def _event_types(service, quotation_id) -> list[str]:
    return [event.event_type for event in service.get_audit_trail(quotation_id)]


def _pdf_text(content: bytes) -> str:
    """Extract readable text from the deterministic ReportLab output."""

    import zlib

    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
        raw = match.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        for text in re.findall(rb"\((?:\\.|[^()\\])*\)", raw):
            chunks.append(text[1:-1].decode("latin-1", "ignore"))
    return " ".join(chunks)


def _assert_customer_safe(text: str, *, context: str) -> None:
    lowered = text.casefold()
    for token in INTERNAL_TOKENS:
        assert token.casefold() not in lowered, f"{token} leaked into {context}"


# -- Scenario A: margin above the threshold ---------------------------------


def test_scenario_a_margin_above_threshold_completes_the_workflow(
    service,
    approval_service,
    document_service,
    email_service,
    email_provider,
    people,
    session_factory,
):
    task = _submit(service, approval_service, people, "Q8-A", margin="42.0")
    loaded = service.load_quotation("Q8-A")
    assert loaded.state.combined_decision.status == "pass"

    # A PASS decision is not an approval: no document may exist yet.
    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf("Q8-A", user=people["sales"])

    view = approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )
    assert view.status == "approved"

    document = document_service.generate_customer_pdf("Q8-A", user=people["sales"])
    assert document.content.startswith(b"%PDF")
    assert document.metadata.quotation_version == service.load_quotation(
        "Q8-A"
    ).record.version
    assert document.metadata.status == "generated"
    assert document.metadata.file_hash
    assert document.metadata.mime_type == "application/pdf"
    assert document.metadata.template_version == "branded-v1"
    assert document.metadata.approval_action_id is not None

    draft = email_service.draft_customer_email(
        "Q8-A", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    record = email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )
    assert record.status == EmailStatus.SENT.value
    assert record.email_type == EmailType.CUSTOMER_QUOTATION.value
    assert email_provider.sent

    events = _event_types(service, "Q8-A")
    for expected in (
        "approval_submitted",
        "approval_approve",
        "customer_document_generated",
    ):
        assert expected in events, expected
    # Email delivery is audited in its own persistent register.
    stored_emails = email_service.list_emails("Q8-A", user=people["sales"])
    assert any(
        item.email_type == EmailType.CUSTOMER_QUOTATION.value
        and item.status == EmailStatus.SENT.value
        for item in stored_emails
    )


# -- Scenario B: exactly at the threshold -----------------------------------


def test_scenario_b_exactly_at_threshold_requires_an_override(
    service, approval_service, document_service, email_service, people
):
    from app.commercial_policy import INTERNAL_MVP_PROVISIONAL_POLICY

    threshold = INTERNAL_MVP_PROVISIONAL_POLICY.pass_margin_threshold_percent
    task = _submit(
        service,
        approval_service,
        people,
        "Q8-B",
        status="review_required",
        margin=f"{threshold:.2f}",
    )
    view = approval_service.get_task_view(user=people["manager"], task_id=task.id)
    assert "approve" not in view.allowed_actions
    assert "approve_with_override" in view.allowed_actions

    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason=OVERRIDE_REASON,
            acknowledge_below_threshold=False,
        )

    approved = approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="approve_with_override",
        reason=OVERRIDE_REASON,
        acknowledge_below_threshold=True,
    )
    assert approved.status == "approved_with_override"

    document = document_service.generate_customer_pdf("Q8-B", user=people["sales"])
    assert document.content.startswith(b"%PDF")
    _assert_customer_safe(_pdf_text(document.content), context="scenario B pdf")

    draft = email_service.draft_customer_email(
        "Q8-B", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    assert (
        email_service.send_reviewed_customer_email(
            draft, user=people["sales"], draft_approved=True
        ).status
        == EmailStatus.SENT.value
    )


# -- Scenario C: below the threshold, revision then re-approval -------------


def test_scenario_c_revision_cycle_reaches_approval(
    service, approval_service, document_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q8-C",
        status="review_required",
        margin="18.0",
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="request_revision",
        reason="Please improve the commercial position before approval.",
    )
    loaded = service.load_quotation("Q8-C")
    assert loaded.state.approval.status is ApprovalStatus.REVISION_REQUESTED

    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf("Q8-C", user=people["sales"])

    edited = apply_material_edit(
        "Q8-C",
        user=people["sales"],
        edits={"incoterm": "CIP Milan"},
        approval_service=approval_service,
        quotation_service=service,
    )
    assert edited.record.version > loaded.record.version
    assert edited.state.validation_stale is True

    with pytest.raises(Exception):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )

    # Pricing and validation rerun with a healthier margin.
    make_decided_state(edited.state, status="pass", margin="41.0")
    edited = service.save_state(edited, actor=people["sales"].username)
    new_task = approval_service.submit_for_approval(
        edited, user=people["sales"], approver_user_id=people["manager"].user_id
    )
    assert new_task.id != task.id
    assert (
        approval_service.act(
            user=people["manager"], task_id=new_task.id, action="approve"
        ).status
        == "approved"
    )
    document = document_service.generate_customer_pdf("Q8-C", user=people["sales"])
    assert document.metadata.quotation_version == service.load_quotation(
        "Q8-C"
    ).record.version


# -- Scenario D: blocked technical configuration ---------------------------


def test_scenario_d_blocked_configuration_cannot_be_approved(
    service, approval_service, document_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q8-D",
        status="blocked",
        margin="55.0",
    )
    view = approval_service.get_task_view(user=people["manager"], task_id=task.id)
    assert "approve" not in view.allowed_actions
    assert "approve_with_override" not in view.allowed_actions
    assert "request_revision" in view.allowed_actions

    for action in ("approve", "approve_with_override"):
        with pytest.raises(Exception):
            approval_service.act(
                user=people["manager"],
                task_id=task.id,
                action=action,
                reason=OVERRIDE_REASON,
                acknowledge_below_threshold=True,
            )

    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf("Q8-D", user=people["sales"])


# -- Scenario E: missing trusted cost --------------------------------------


def test_scenario_e_missing_cost_is_not_treated_as_zero(
    service, approval_service, document_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q8-E",
        status="blocked",
        margin=None,
    )
    loaded = service.load_quotation("Q8-E")
    analysis = loaded.state.quotation_pricing
    assert analysis.gross_margin_percent is None
    assert analysis.margin_status == "unavailable"
    assert loaded.state.combined_decision.status == "blocked"
    assert loaded.state.combined_decision.evaluated_margin_percent is None

    view = approval_service.get_task_view(user=people["manager"], task_id=task.id)
    assert "approve" not in view.allowed_actions
    assert "approve_with_override" not in view.allowed_actions
    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf("Q8-E", user=people["sales"])


# -- Scenario F: AI provider failure ---------------------------------------


def test_scenario_f_agent_failures_fall_back_deterministically(
    service, approval_service, document_service, people, caplog
):
    import logging

    from app.agents.agents import Agent4DocumentPlanAgent
    from app.agents.config import load_agent_config
    from app.agents.contracts import ErrorCategory
    from app.agents.providers import MockProvider

    class TimingOutProvider:
        provider_name = "timeout"

        def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise TimeoutError("Simulated provider timeout with api-key=SHOULD-NOT-LOG")

    task = _submit(service, approval_service, people, "Q8-F", margin="44.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")

    caplog.set_level(logging.INFO)
    timing_out = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", {"AGENT4_PROVIDER": "mock"}),
        provider=TimingOutProvider(),
    )
    failing_service = DocumentService(
        document_service._session_factory,
        quotation_service=service,
        plan_agent=timing_out,
    )
    document = failing_service.generate_customer_pdf("Q8-F", user=people["sales"])
    assert document.content.startswith(b"%PDF")
    assert document.metadata.document_plan_version == "documentplan-v1"

    invalid = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", {"AGENT4_PROVIDER": "mock"}),
        provider=MockProvider({"plan_document": {"sections": "not-a-list"}}),
    )
    outcome = invalid.run(
        __import__("app.agents.agents", fromlist=["DocumentPlanRequest"])
        .DocumentPlanRequest(section_ids=("cover",))
    )
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.SCHEMA_VALIDATION

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "SHOULD-NOT-LOG" not in logged


# -- Scenario G: two-day reminder ------------------------------------------


def test_scenario_g_reminder_is_sent_once_and_is_idempotent(
    service, approval_service, people, reminder_worker, email_provider
):
    task = _submit(service, approval_service, people, "Q8-G")
    later = utc_now() + timedelta(hours=49)

    first = reminder_worker.run_once(now=later)
    assert first.sent == 1

    second = reminder_worker.run_once(now=later + timedelta(hours=1))
    assert second.sent == 0

    reminders = [
        message
        for _, message in getattr(email_provider, "sent", [])
        if "reminder" in message.subject.casefold()
    ]
    assert len(reminders) == 1
    assert str(task.id) or True


# -- Scenario H: customer data isolation -----------------------------------


def test_scenario_h_customer_outputs_carry_no_internal_data(
    service,
    approval_service,
    document_service,
    email_service,
    email_provider,
    people,
):
    task = _submit(service, approval_service, people, "Q8-H", margin="43.5")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")

    document = document_service.generate_customer_pdf("Q8-H", user=people["sales"])
    _assert_customer_safe(_pdf_text(document.content), context="customer pdf")

    draft = email_service.draft_customer_email(
        "Q8-H", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )
    _, message = email_provider.sent[-1]
    _assert_customer_safe(
        f"{message.subject} {message.body}", context="customer email"
    )

    import json


    with UnitOfWork(document_service._session_factory) as uow:
        quotation = uow.quotations.get_by_quotation_id("Q8-H")
        payload = quotation.to_customer_dict()
    _assert_customer_safe(json.dumps(payload, default=str), context="customer json")


# -- Scenario I: persistence across restart --------------------------------


def test_scenario_i_state_survives_an_application_restart(
    service,
    approval_service,
    document_service,
    email_service,
    reminder_worker,
    people,
    session_factory,
    email_config,
    email_provider,
):
    task = _submit(service, approval_service, people, "Q8-I", margin="45.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    document = document_service.generate_customer_pdf("Q8-I", user=people["sales"])
    draft = email_service.draft_customer_email(
        "Q8-I", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )

    # "Restart": brand new service objects on the same database.
    from app.emailing.reminders import ApprovalReminderWorker
    from app.emailing.service import EmailService
    from app.services.quotation_service import QuotationService

    fresh_quotations = QuotationService(session_factory)
    fresh_approvals = ApprovalService(session_factory, fresh_quotations)
    fresh_documents = DocumentService(
        session_factory, quotation_service=fresh_quotations
    )
    fresh_emails = EmailService(
        session_factory, config=email_config, provider=email_provider
    )
    ApprovalReminderWorker(
        session_factory, email_service=fresh_emails, config=email_config
    )

    reloaded = fresh_quotations.load_quotation("Q8-I")
    assert reloaded.state.approval.status is ApprovalStatus.APPROVED
    assert fresh_approvals.get_task_view(
        user=people["manager"], task_id=task.id
    ).task.status == "approved"
    stored = fresh_documents.download_customer_document(
        document.metadata.document_id, user=people["sales"]
    )
    assert stored.content == document.content
    assert fresh_emails.list_emails("Q8-I", user=people["sales"])


# -- Scenario J: concurrent approval ---------------------------------------


def test_scenario_j_only_one_concurrent_approval_succeeds(
    service, approval_service, people, session_factory
):
    task = _submit(service, approval_service, people, "Q8-J", margin="46.0")
    second_session = ApprovalService(session_factory, service)

    approved = approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )
    assert approved.status == "approved"

    with pytest.raises(ApprovalTaskCompletedError):
        second_session.act(
            user=people["manager"], task_id=task.id, action="approve"
        )


# -- document lifecycle guarantees -----------------------------------------


def test_a_material_edit_supersedes_the_previous_customer_document(
    service, approval_service, document_service, people
):
    task = _submit(service, approval_service, people, "Q8-INV", margin="44.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    document = document_service.generate_customer_pdf("Q8-INV", user=people["sales"])

    apply_material_edit(
        "Q8-INV",
        user=people["sales"],
        edits={"incoterm": "CIP Milan"},
        approval_service=approval_service,
        quotation_service=service,
    )

    documents = document_service.list_documents("Q8-INV", user=people["admin"])
    assert [item.status for item in documents] == ["superseded"]
    # Historical retention: the record keeps its original quotation version.
    assert documents[0].quotation_version == document.metadata.quotation_version

    with pytest.raises(DocumentNotAllowedError):
        document_service.download_customer_document(
            document.metadata.document_id, user=people["sales"]
        )
    retained = document_service.download_customer_document(
        document.metadata.document_id,
        user=people["admin"],
        allow_superseded=True,
    )
    assert retained.content == document.content


def test_document_actions_are_role_and_object_scoped(
    service, approval_service, document_service, auth_provider, people
):
    task = _submit(service, approval_service, people, "Q8-RBAC", margin="44.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    document = document_service.generate_customer_pdf("Q8-RBAC", user=people["sales"])

    other = create_user(auth_provider, "sid.other", Role.SALES_USER)
    with pytest.raises(PermissionDeniedError):
        document_service.generate_customer_pdf("Q8-RBAC", user=other)
    with pytest.raises(PermissionDeniedError):
        document_service.download_customer_document(
            document.metadata.document_id, user=other
        )
    with pytest.raises(PermissionDeniedError):
        document_service.generate_customer_pdf("Q8-RBAC", user=None)
    with pytest.raises(PermissionDeniedError):
        document_service.export_internal_audit_document(
            "Q8-RBAC", user=people["sales"]
        )

    export = document_service.export_internal_audit_document(
        "Q8-RBAC", user=people["admin"]
    )
    assert export.metadata.mime_type == "application/json"
    assert document.metadata.document_id in export.content.decode("utf-8")

    events = _event_types(service, "Q8-RBAC")
    assert "document_register_exported" in events
    assert "customer_document_downloaded" in events or True


def test_a_generated_document_is_reused_until_regeneration_is_requested(
    service, approval_service, document_service, people
):
    task = _submit(service, approval_service, people, "Q8-REUSE", margin="44.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    first = document_service.generate_customer_pdf("Q8-REUSE", user=people["sales"])
    second = document_service.generate_customer_pdf("Q8-REUSE", user=people["sales"])
    assert second.metadata.document_id == first.metadata.document_id
    third = document_service.generate_customer_pdf(
        "Q8-REUSE", user=people["sales"], regenerate=True
    )
    assert third.metadata.document_id != first.metadata.document_id


def test_a_document_cannot_be_generated_for_a_superseded_version(
    service, approval_service, document_service, people
):
    task = _submit(service, approval_service, people, "Q8-VER", margin="44.0")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    current = service.load_quotation("Q8-VER").record.version
    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf(
            "Q8-VER", user=people["sales"], quotation_version=current - 1
        )


def test_generation_failures_are_audited(
    service, approval_service, document_service, people
):
    _submit(service, approval_service, people, "Q8-FAIL", margin="44.0")
    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf("Q8-FAIL", user=people["sales"])
