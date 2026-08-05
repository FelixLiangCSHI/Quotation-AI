"""The Approval Center lists pending tasks and offers approval actions."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.ui import approval_page
from tests.fixtures.phase7_helpers import submit_quotation


class _Recorder:
    """Minimal stand-in for the Streamlit API used by the approval page."""

    def __init__(self, *, click_approve: bool = False) -> None:
        self.texts: list[str] = []
        self.click_approve = click_approve
        self.form_submitted = False

    # -- text output ---------------------------------------------------

    def _record(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            self.texts.append(args[0])
        return None

    title = subheader = caption = markdown = write = _record
    info = warning = error = success = _record
    metric = _record
    text_area = _record
    dataframe = _record

    def checkbox(self, *args, **kwargs) -> bool:
        return True

    def columns(self, count, **kwargs):
        return [self for _ in range(count if isinstance(count, int) else len(count))]

    @contextmanager
    def container(self, *args, **kwargs):
        yield self

    @contextmanager
    def form(self, *args, **kwargs):
        yield self

    def form_submit_button(self, label, **kwargs) -> bool:
        if self.click_approve and label == "Approve" and not self.form_submitted:
            self.form_submitted = True
            return True
        return False

    def rerun(self) -> None:
        raise _Rerun


class _Rerun(RuntimeError):
    """Raised in place of ``st.rerun``."""


@pytest.fixture()
def recorder(monkeypatch):
    fake = _Recorder()
    monkeypatch.setattr(approval_page, "st", fake)
    return fake


def test_the_approval_center_lists_a_pending_task(
    monkeypatch, recorder, service, approval_service, people
):
    submit_quotation(service, approval_service, people, "Q-UI-1")
    monkeypatch.setattr(
        approval_page, "ApprovalService", lambda: approval_service
    )

    approval_page.render(people["manager"])

    assert any("Q-UI-1" in text for text in recorder.texts)
    assert any("awaiting your decision" in text for text in recorder.texts)


def test_the_approval_center_records_a_decision(
    monkeypatch, service, approval_service, people
):
    task = submit_quotation(service, approval_service, people, "Q-UI-2")
    monkeypatch.setattr(approval_page, "st", _Recorder(click_approve=True))
    monkeypatch.setattr(
        approval_page, "ApprovalService", lambda: approval_service
    )

    with pytest.raises(_Rerun):
        approval_page.render(people["manager"])

    assert service.load_quotation("Q-UI-2").record.approval_status == "approved"
    assert approval_service.list_tasks(people["manager"]) == ()
    completed = approval_service.list_tasks(
        people["manager"], only_open=False, assigned_to_me=False
    )
    assert [item.id for item in completed] == [task.id]


def test_the_approval_history_only_shows_completed_tasks(
    monkeypatch, recorder, service, approval_service, people
):
    submit_quotation(service, approval_service, people, "Q-UI-3")
    monkeypatch.setattr(
        approval_page, "ApprovalService", lambda: approval_service
    )

    approval_page.render_history(people["manager"])

    assert any(
        "No approval decision has been recorded yet." in text
        for text in recorder.texts
    )
