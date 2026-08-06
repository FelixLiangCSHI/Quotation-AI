"""The demo path, end to end, through the real services.

Each scenario follows exactly what the Streamlit demo shows: a sales user
submits a multi-line quotation, the assigned manager captures and decides the
task from the Approval Center, and only then can the sales user generate the
persistent customer PDF and send the reviewed customer email.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.emailing.contracts import EmailStatus
from app.emailing.service import EmailNotAllowedError
from app.quotation_models import ApprovalStatus
from app.approval_workflow import ApprovalWorkflowError
from app.services.approval_service import MissingJustificationError
from app.services.document_service import DocumentNotAllowedError
from app.ui import approval_page
from tests.fixtures.phase6_helpers import make_decided_state
from tests.fixtures.phase7_helpers import CUSTOMER_ADDRESS, add_line_items

OVERRIDE_REASON = (
    "Strategic account. I acknowledge the quotation margin is equal to or "
    "below the configured policy threshold and accept the commercial risk."
)


def _submit(
    service,
    approval_service,
    people,
    quotation_id,
    *,
    status="pass",
    margin="42.0",
):
    """Sales creates a multi-line quotation and submits it to the manager."""

    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    make_decided_state(loaded.state, status=status, margin=margin)
    add_line_items(loaded.state)
    loaded = service.save_state(loaded, actor=people["sales"].username)
    assert len(loaded.state.draft.line_items) > 1
    return approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people["manager"].user_id,
    )


def _capture(approval_service, people, quotation_id):
    """The manager finds and opens the task exactly as the UI does."""

    tasks = approval_service.list_tasks(people["manager"])
    matching = [
        task for task in tasks if task.quotation_reference == quotation_id
    ]
    assert len(matching) == 1
    view = approval_service.get_task_view(people["manager"], matching[0].id)
    assert view.quotation_id == quotation_id
    return matching[0], view


def _send_customer_email(email_service, people, quotation_id):
    draft = email_service.draft_customer_email(
        quotation_id, user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    assert draft.record.status == EmailStatus.PENDING_REVIEW.value
    with pytest.raises(EmailNotAllowedError):
        email_service.send_reviewed_customer_email(
            draft, user=people["sales"], draft_approved=False
        )
    return email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )


# -- Scenario A: a PASS quotation is approved and fully delivered -----------


def test_scenario_a_pass_quotation_runs_the_whole_demo_path(
    service,
    approval_service,
    document_service,
    email_service,
    people,
):
    task = _submit(service, approval_service, people, "Q-DEMO-A")
    captured, view = _capture(approval_service, people, "Q-DEMO-A")
    assert captured.id == task.id
    assert view.decision_status == "pass"
    assert "approve" in view.allowed_actions

    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    reloaded = service.load_quotation("Q-DEMO-A")
    assert reloaded.state.approval.status is ApprovalStatus.APPROVED
    assert reloaded.record.approval_status == "approved"

    document = document_service.generate_customer_pdf(
        "Q-DEMO-A", user=people["sales"]
    )
    assert document.content.startswith(b"%PDF")
    listed = document_service.list_documents("Q-DEMO-A", user=people["sales"])
    assert document.metadata.document_id in {
        item.document_id for item in listed
    }
    downloaded = document_service.download_customer_document(
        document.metadata.document_id, user=people["sales"]
    )
    assert downloaded.content.startswith(b"%PDF")

    record = _send_customer_email(email_service, people, "Q-DEMO-A")
    assert record.status == EmailStatus.SENT.value
    persisted = email_service.list_emails("Q-DEMO-A", user=people["sales"])
    assert record.email_id in {
        item.email_id for item in persisted
    }

    events = {
        event.event_type for event in service.get_audit_trail("Q-DEMO-A")
    }
    assert {
        "approval_submitted",
        "approver_assigned",
        "approval_approve",
        "customer_document_generated",
    } <= events


# -- Scenario B: REVIEW_REQUIRED needs a justified override ------------------


def test_scenario_b_review_required_only_completes_with_a_full_override(
    service,
    approval_service,
    document_service,
    email_service,
    people,
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q-DEMO-B",
        status="review_required",
        margin="18.0",
    )
    _, view = _capture(approval_service, people, "Q-DEMO-B")
    assert view.decision_status == "review_required"
    assert "approve" not in view.allowed_actions

    with pytest.raises(ApprovalWorkflowError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )
    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason="",
            acknowledge_below_threshold=True,
        )
    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason=OVERRIDE_REASON,
            acknowledge_below_threshold=False,
        )

    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="approve_with_override",
        reason=OVERRIDE_REASON,
        acknowledge_below_threshold=True,
    )
    assert (
        service.load_quotation("Q-DEMO-B").state.approval.status
        is ApprovalStatus.APPROVED_WITH_OVERRIDE
    )

    document = document_service.generate_customer_pdf(
        "Q-DEMO-B", user=people["sales"]
    )
    assert document.content.startswith(b"%PDF")
    assert (
        _send_customer_email(email_service, people, "Q-DEMO-B").status
        == EmailStatus.SENT.value
    )


# -- Scenario C: a BLOCKED quotation produces nothing ------------------------


def test_scenario_c_blocked_quotation_can_never_reach_a_customer(
    service,
    approval_service,
    document_service,
    email_service,
    people,
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q-DEMO-C",
        status="blocked",
        margin="5.0",
    )
    _, view = _capture(approval_service, people, "Q-DEMO-C")
    assert view.decision_status == "blocked"
    assert "approve" not in view.allowed_actions
    assert "approve_with_override" not in view.allowed_actions

    with pytest.raises(ApprovalWorkflowError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )
    with pytest.raises(ApprovalWorkflowError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason=OVERRIDE_REASON,
            acknowledge_below_threshold=True,
        )

    with pytest.raises(DocumentNotAllowedError):
        document_service.generate_customer_pdf(
            "Q-DEMO-C", user=people["sales"]
        )
    with pytest.raises(EmailNotAllowedError):
        email_service.draft_customer_email(
            "Q-DEMO-C",
            user=people["sales"],
            recipients=(CUSTOMER_ADDRESS,),
        )


# -- Approval Center regression ---------------------------------------------


class _Recorder:
    """Minimal Streamlit stand-in that records every rendered string."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def _record(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, str):
                self.texts.append(arg)
        return None

    title = subheader = caption = markdown = write = _record
    info = warning = error = success = metric = text_area = _record

    def dataframe(self, data, *args, **kwargs):
        self.texts.append(repr(data))
        return None

    def checkbox(self, *args, **kwargs) -> bool:
        return False

    def columns(self, count, **kwargs):
        width = count if isinstance(count, int) else len(count)
        return [self for _ in range(width)]

    @contextmanager
    def container(self, *args, **kwargs):
        yield self

    @contextmanager
    def form(self, *args, **kwargs):
        yield self

    def form_submit_button(self, *args, **kwargs) -> bool:
        return False

    def rerun(self) -> None:  # pragma: no cover - never reached here
        raise AssertionError("No action was triggered.")


@pytest.fixture()
def approval_ui(monkeypatch, approval_service):
    recorder = _Recorder()
    monkeypatch.setattr(approval_page, "st", recorder)
    monkeypatch.setattr(
        approval_page, "ApprovalService", lambda: approval_service
    )
    return recorder


def test_the_approval_center_renders_the_pending_task_not_only_a_title(
    service, approval_service, people, approval_ui
):
    _submit(service, approval_service, people, "Q-DEMO-UI-1")

    approval_page.render(people["manager"])

    assert any("Q-DEMO-UI-1" in text for text in approval_ui.texts)
    assert any("awaiting your decision" in text for text in approval_ui.texts)


def test_the_approval_history_shows_completed_tasks_only(
    service, approval_service, people, approval_ui
):
    task = _submit(service, approval_service, people, "Q-DEMO-UI-2")
    _submit(service, approval_service, people, "Q-DEMO-UI-3")
    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    approval_page.render_history(people["manager"])

    rendered = " ".join(approval_ui.texts)
    assert "Q-DEMO-UI-2" in rendered
    assert "Q-DEMO-UI-3" not in rendered
