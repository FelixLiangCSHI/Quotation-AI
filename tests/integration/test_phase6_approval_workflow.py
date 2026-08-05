"""Phase 6: persistent, authenticated approval workflow end to end."""

from __future__ import annotations

import pytest

from app.auth import PermissionDeniedError, Role
from app.db import models
from app.quotation_models import ApprovalStatus, WorkflowStage
from app.services.approval_service import (
    ApprovalService,
    ApprovalServiceError,
    ApprovalTaskCompletedError,
    MissingJustificationError,
    StaleApprovalTaskError,
    allowed_actions_for,
    apply_material_edit,
)
from app.approval_workflow import InvalidApprovalTransitionError
from tests.fixtures.phase6_helpers import (
    POLICY_VERSION_ID,
    THRESHOLD,
    make_decided_state,
)

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
    status,
    margin,
    approver="manager",
):
    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    make_decided_state(loaded.state, status=status, margin=margin)
    loaded = service.save_state(loaded, actor=people["sales"].username)
    task = approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people[approver].user_id,
    )
    return task


# -- deterministic action matrix --------------------------------------


def test_allowed_actions_are_derived_from_the_decision():
    assert allowed_actions_for("pass") == ("approve", "request_revision")
    assert allowed_actions_for("review_required") == (
        "approve_with_override",
        "request_revision",
        "reject",
    )
    assert allowed_actions_for("blocked") == ("request_revision", "reject")


# -- submission --------------------------------------------------------


def test_submission_requires_a_stored_internal_approver(
    service, approval_service, people
):
    loaded = service.create_quotation(quotation_id="Q6-SUB-1")
    make_decided_state(loaded.state, status="pass", margin="42.0")
    loaded = service.save_state(loaded)

    with pytest.raises(ApprovalServiceError):
        approval_service.submit_for_approval(
            loaded, user=people["sales"], approver_user_id=999_999
        )


def test_submission_refuses_a_user_without_an_approver_role(
    service, approval_service, people
):
    loaded = service.create_quotation(quotation_id="Q6-SUB-2")
    make_decided_state(loaded.state, status="pass", margin="42.0")
    loaded = service.save_state(loaded)

    with pytest.raises(ApprovalServiceError):
        approval_service.submit_for_approval(
            loaded,
            user=people["sales"],
            approver_user_id=people["sales"].user_id,
        )


def test_approver_candidates_come_from_stored_users(approval_service, people):
    candidates = approval_service.list_possible_approvers(people["sales"])

    usernames = {candidate.username for candidate in candidates}
    assert usernames == {"mia.manager", "pat.pricing"}


def test_submitted_task_records_the_full_reference_set(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-SUB-3", status="pass", margin="42.0"
    )

    assert task.task_reference
    assert task.quotation_reference == "Q6-SUB-3"
    assert task.quotation_version >= 1
    assert task.decision_status == "pass"
    assert task.assigned_approver_role == Role.SALES_MANAGER.value
    assert task.assigned_user_id == people["manager"].user_id
    assert task.status == "pending_review"
    assert task.submitted_at is not None
    assert task.reminder_due_at is not None
    assert task.policy_version_id == POLICY_VERSION_ID
    assert task.pricing_run_id == "PR-1"
    assert task.validation_run_id == "TV-1"


# -- PASS workflow ------------------------------------------------------


def test_pass_creates_a_task_and_is_not_auto_approved(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-PASS-1", status="pass", margin="42.0"
    )

    assert task.status == "pending_review"
    record = service.load_quotation("Q6-PASS-1").record
    assert record.approval_status == ApprovalStatus.PENDING_REVIEW.value
    assert record.approval_status != ApprovalStatus.APPROVED.value


def test_authorised_manager_can_approve_a_pass_quotation(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-PASS-2", status="pass", margin="42.0"
    )

    completed = approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    assert completed.status == ApprovalStatus.APPROVED.value
    assert completed.completed_at is not None
    reopened = service.load_quotation("Q6-PASS-2")
    assert reopened.record.approval_status == ApprovalStatus.APPROVED.value
    assert reopened.record.status == WorkflowStage.APPROVED.value
    assert reopened.state.approval.actor == "mia.manager"


def test_sales_user_cannot_approve(service, approval_service, people):
    task = _submit(
        service, approval_service, people, "Q6-PASS-3", status="pass", margin="42.0"
    )

    with pytest.raises(PermissionDeniedError):
        approval_service.act(
            user=people["sales"], task_id=task.id, action="approve"
        )

    assert (
        service.load_quotation("Q6-PASS-3").record.approval_status
        == ApprovalStatus.PENDING_REVIEW.value
    )


def test_pass_approval_survives_a_restart(
    service, approval_service, people, session_factory
):
    task = _submit(
        service, approval_service, people, "Q6-PASS-4", status="pass", margin="42.0"
    )
    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    # A fresh service instance stands in for a restarted process.
    from app.services.quotation_service import QuotationService

    restarted = QuotationService(session_factory)
    assert (
        restarted.load_quotation("Q6-PASS-4").record.approval_status
        == ApprovalStatus.APPROVED.value
    )


def test_pass_override_action_is_unavailable(service, approval_service, people):
    task = _submit(
        service, approval_service, people, "Q6-PASS-5", status="pass", margin="42.0"
    )

    with pytest.raises(InvalidApprovalTransitionError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason=OVERRIDE_REASON,
            acknowledge_below_threshold=True,
        )


# -- REVIEW_REQUIRED workflow -------------------------------------------


@pytest.mark.parametrize("margin", ["35.0", "34.9", "12.5"])
def test_margin_at_or_below_threshold_cannot_use_normal_approval(
    service, approval_service, people, margin
):
    task = _submit(
        service,
        approval_service,
        people,
        f"Q6-REV-{margin}",
        status="review_required",
        margin=margin,
    )

    view = approval_service.get_task_view(people["manager"], task.id)
    assert "approve" not in view.allowed_actions
    assert view.allowed_actions == (
        "approve_with_override",
        "request_revision",
        "reject",
    )
    with pytest.raises(InvalidApprovalTransitionError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )


def test_override_requires_a_written_justification(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-REV-2",
        status="review_required",
        margin="35.0",
    )

    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason="",
            acknowledge_below_threshold=True,
        )


def test_override_requires_acknowledging_the_threshold(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-REV-3",
        status="review_required",
        margin="35.0",
    )

    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason="Fine by me.",
            acknowledge_below_threshold=False,
        )


def test_valid_override_is_persisted_with_its_full_record(
    service, approval_service, people, session_factory
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-REV-4",
        status="review_required",
        margin="35.0",
        approver="pricing",
    )

    completed = approval_service.act(
        user=people["pricing"],
        task_id=task.id,
        action="approve_with_override",
        reason=OVERRIDE_REASON,
        acknowledge_below_threshold=True,
    )

    assert completed.status == ApprovalStatus.APPROVED_WITH_OVERRIDE.value
    with session_factory() as session:
        override = session.query(models.ApprovalOverrideRecord).one()
    assert override.original_decision == "review_required"
    assert override.evaluated_margin_percent == "35.0"
    assert override.policy_threshold_percent == THRESHOLD
    assert override.policy_version_id == POLICY_VERSION_ID
    assert override.approver_name == "pat.pricing"
    assert override.approver_role == Role.PRICING_MANAGER.value
    assert override.justification == OVERRIDE_REASON
    assert override.final_approved_price is not None
    assert override.final_margin_percent == "35.0"
    assert override.triggered_rule_ids == ["COMM-MARGIN-002"]
    assert override.occurred_at is not None


def test_revision_request_below_threshold_requires_a_reason(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-REV-5",
        status="review_required",
        margin="20.0",
    )

    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="request_revision"
        )

    completed = approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="request_revision",
        reason="Rework the discount structure and resubmit.",
    )
    assert completed.status == ApprovalStatus.REVISION_REQUESTED.value


# -- BLOCKED workflow ---------------------------------------------------


def test_blocked_quotation_cannot_be_approved_in_any_form(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-BLK-1",
        status="blocked",
        margin=None,
    )

    view = approval_service.get_task_view(people["manager"], task.id)
    assert view.allowed_actions == ("request_revision", "reject")
    for action in ("approve", "approve_with_override"):
        with pytest.raises(InvalidApprovalTransitionError):
            approval_service.act(
                user=people["manager"],
                task_id=task.id,
                action=action,
                reason=OVERRIDE_REASON,
                acknowledge_below_threshold=True,
            )


def test_blocked_quotation_allows_revision_and_rejection(
    service, approval_service, people
):
    revision_task = _submit(
        service,
        approval_service,
        people,
        "Q6-BLK-2",
        status="blocked",
        margin=None,
    )
    completed = approval_service.act(
        user=people["manager"],
        task_id=revision_task.id,
        action="request_revision",
        reason="Resolve the technical incompatibility first.",
    )
    assert completed.status == ApprovalStatus.REVISION_REQUESTED.value

    reject_task = _submit(
        service,
        approval_service,
        people,
        "Q6-BLK-3",
        status="blocked",
        margin=None,
    )
    rejected = approval_service.act(
        user=people["manager"],
        task_id=reject_task.id,
        action="reject",
        reason="The configuration cannot be supplied.",
    )
    assert rejected.status == ApprovalStatus.REJECTED.value


# -- concurrency and idempotency ---------------------------------------


def test_completed_task_cannot_be_completed_again(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-CON-1", status="pass", margin="42.0"
    )
    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    with pytest.raises(ApprovalTaskCompletedError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )


def test_duplicate_action_id_is_rejected(service, approval_service, people):
    task = _submit(
        service, approval_service, people, "Q6-CON-2", status="pass", margin="42.0"
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="approve",
        action_id="replayed-request",
    )

    with pytest.raises(ApprovalTaskCompletedError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve",
            action_id="replayed-request",
        )


def test_stale_quotation_version_is_rejected(
    service, approval_service, people, session_factory
):
    task = _submit(
        service, approval_service, people, "Q6-CON-3", status="pass", margin="42.0"
    )
    # A later write bumps the quotation version behind the task's back.
    service.save_state(service.load_quotation("Q6-CON-3"))

    with pytest.raises(StaleApprovalTaskError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )

    with session_factory() as session:
        assert session.query(models.ApprovalAction).count() == 0


def test_stale_pricing_run_is_rejected(service, approval_service, people):
    task = _submit(
        service, approval_service, people, "Q6-CON-4", status="pass", margin="42.0"
    )
    loaded = service.load_quotation("Q6-CON-4")
    loaded.state.combined_decision.pricing_run_id = "PR-2"
    service.save_state(loaded)
    # Realign the version so only the pricing run differs.
    refreshed = service.load_quotation("Q6-CON-4")
    with UnitOfWorkVersion(service, refreshed, task):
        with pytest.raises(StaleApprovalTaskError):
            approval_service.act(
                user=people["manager"], task_id=task.id, action="approve"
            )


class UnitOfWorkVersion:
    """Align the persisted task version with the current quotation version.

    This isolates the pricing-run staleness check from the version check.
    """

    def __init__(self, service, loaded, task):
        self._service = service
        self._loaded = loaded
        self._task = task

    def __enter__(self):
        from app.services.unit_of_work import UnitOfWork
        from app.db import models as orm

        with UnitOfWork(self._service._session_factory) as uow:
            row = uow.session.get(orm.ApprovalTask, self._task.id)
            row.quotation_version = self._loaded.version
            uow.commit()
        return self

    def __exit__(self, *exc_info):
        return False


def test_a_failed_action_rolls_everything_back(
    service, approval_service, people, session_factory
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-CON-5",
        status="review_required",
        margin="30.0",
    )

    with pytest.raises(MissingJustificationError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason="",
            acknowledge_below_threshold=True,
        )

    with session_factory() as session:
        assert session.query(models.ApprovalAction).count() == 0
        assert session.query(models.ApprovalOverrideRecord).count() == 0
        assert (
            session.query(models.ApprovalTask).one().status == "pending_review"
        )


def test_a_task_assigned_to_another_approver_is_refused(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-CON-6",
        status="pass",
        margin="42.0",
        approver="manager",
    )

    with pytest.raises(PermissionDeniedError):
        approval_service.act(
            user=people["pricing"], task_id=task.id, action="approve"
        )


def test_a_price_change_must_go_through_a_controlled_edit(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-CON-7", status="pass", margin="42.0"
    )

    with pytest.raises(InvalidApprovalTransitionError):
        approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve",
            final_unit_price=90_000.0,
        )


# -- material edits ------------------------------------------------------


def test_material_edit_invalidates_the_task_and_the_approval(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-EDIT-1", status="pass", margin="42.0"
    )

    apply_material_edit(
        "Q6-EDIT-1",
        user=people["sales"],
        edits={"quantity": 4},
        approval_service=approval_service,
        quotation_service=service,
    )

    refreshed = approval_service.list_tasks(
        people["manager"], only_open=False
    )
    assert [item.status for item in refreshed] == ["cancelled_stale"]
    reopened = service.load_quotation("Q6-EDIT-1")
    assert reopened.state.approval.status is ApprovalStatus.NOT_READY
    assert reopened.state.combined_decision is None
    assert reopened.state.quotation_pricing is None
    assert reopened.state.customer_email is None
    assert reopened.state.validation_stale is True
    assert reopened.record.version > task.quotation_version

    with pytest.raises(ApprovalTaskCompletedError):
        approval_service.act(
            user=people["manager"], task_id=task.id, action="approve"
        )


def test_material_edit_after_approval_invalidates_the_approval(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-EDIT-2", status="pass", margin="42.0"
    )
    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )

    apply_material_edit(
        "Q6-EDIT-2",
        user=people["sales"],
        edits={"quantity": 7},
        approval_service=approval_service,
        quotation_service=service,
    )

    reopened = service.load_quotation("Q6-EDIT-2")
    assert reopened.record.approval_status == ApprovalStatus.NOT_READY.value


def test_repricing_and_resubmission_are_required_after_an_edit(
    service, approval_service, people
):
    _submit(
        service, approval_service, people, "Q6-EDIT-3", status="pass", margin="42.0"
    )
    edited = apply_material_edit(
        "Q6-EDIT-3",
        user=people["sales"],
        edits={"quantity": 3},
        approval_service=approval_service,
        quotation_service=service,
    )

    with pytest.raises(ApprovalServiceError):
        approval_service.submit_for_approval(
            edited,
            user=people["sales"],
            approver_user_id=people["manager"].user_id,
        )

    # Rerunning pricing, validation and the logical judgement restores it.
    make_decided_state(edited.state, status="pass", margin="41.0")
    saved = service.save_state(edited)
    task = approval_service.submit_for_approval(
        saved,
        user=people["sales"],
        approver_user_id=people["manager"].user_id,
    )
    assert task.status == "pending_review"


# -- approver review page ------------------------------------------------


def test_task_view_contains_everything_an_approver_needs(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-VIEW-1",
        status="review_required",
        margin="35.0",
        approver="pricing",
    )

    view = approval_service.get_task_view(people["pricing"], task.id)

    assert view.quotation_id == "Q6-VIEW-1"
    assert view.owner_user_id == people["sales"].user_id
    assert view.owner_username == "sam.sales"
    assert view.total_revenue == "100000.00"
    assert view.total_cost == "60000.00"
    assert view.gross_margin_percent == "35.0"
    assert view.threshold_percent == THRESHOLD
    assert view.decision_status == "review_required"
    assert view.technical_validation_status == "pass"
    assert view.triggered_rule_ids == ("COMM-MARGIN-002",)
    assert view.policy_version_id == POLICY_VERSION_ID


def test_total_cost_is_hidden_from_a_role_without_commercial_detail(
    service, approval_service, people
):
    task = _submit(
        service,
        approval_service,
        people,
        "Q6-VIEW-2",
        status="pass",
        margin="42.0",
    )

    view = approval_service.get_task_view(people["manager"], task.id)

    assert view.total_revenue == "100000.00"
    assert view.total_cost is None


def test_a_sales_user_cannot_open_the_approver_page(
    service, approval_service, people
):
    task = _submit(
        service, approval_service, people, "Q6-VIEW-3", status="pass", margin="42.0"
    )

    with pytest.raises(PermissionDeniedError):
        approval_service.get_task_view(people["sales"], task.id)


def test_approver_sees_the_task_from_a_separate_session(
    service, approval_service, people, auth_provider
):
    from tests.fixtures.phase6_helpers import PASSWORD

    task = _submit(
        service, approval_service, people, "Q6-VIEW-4", status="pass", margin="42.0"
    )
    separate_session = auth_provider.authenticate("mia.manager", PASSWORD)
    resolved = auth_provider.resolve_session(separate_session.session_token)

    pending = approval_service.list_tasks(resolved)

    assert [item.id for item in pending] == [task.id]


def test_a_stale_task_exposes_no_actions(service, approval_service, people):
    task = _submit(
        service, approval_service, people, "Q6-VIEW-5", status="pass", margin="42.0"
    )
    service.save_state(service.load_quotation("Q6-VIEW-5"))

    view = approval_service.get_task_view(people["manager"], task.id)

    assert view.is_stale
    assert view.allowed_actions == ()
