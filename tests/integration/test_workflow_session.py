"""The UI session must hold references only, never trusted workflow state."""

from __future__ import annotations

import pytest

from app.quotation_models import QuotationWorkflowState
from app.repositories.interfaces import QuotationVersionConflictError
from app.services.session_reference import (
    ACTIVE_QUOTATION_KEY,
    ACTIVE_QUOTATION_VERSION_KEY,
    SESSION_REFERENCE_KEYS,
    read_session_reference,
)
from app.services.workflow_session import (
    get_active_quotation,
    open_quotation,
    persist_workflow_state,
    start_new_quotation,
)


def test_first_access_creates_and_references_a_persisted_quotation(service):
    session_state: dict = {}

    loaded = get_active_quotation(session_state, service)

    assert session_state[ACTIVE_QUOTATION_KEY] == loaded.quotation_id
    assert session_state[ACTIVE_QUOTATION_VERSION_KEY] == 1
    assert service.load_quotation(loaded.quotation_id).record.version == 1


def test_session_state_never_holds_a_workflow_state_object(service):
    session_state: dict = {}
    get_active_quotation(session_state, service)

    assert set(session_state) <= set(SESSION_REFERENCE_KEYS)
    for value in session_state.values():
        assert not isinstance(value, QuotationWorkflowState)
        assert isinstance(value, (str, int, type(None)))


def test_state_is_reloaded_from_the_database_not_the_session(service):
    session_state: dict = {}
    loaded = get_active_quotation(session_state, service)
    loaded.state.draft.customer_name = "Synthetic Hospital"
    persist_workflow_state(session_state, loaded.state, service)

    # A brand new session object pointing at the same quotation.
    other_session = {ACTIVE_QUOTATION_KEY: loaded.quotation_id}
    reopened = get_active_quotation(other_session, service)

    assert reopened.state.draft.customer_name == "Synthetic Hospital"
    assert other_session[ACTIVE_QUOTATION_VERSION_KEY] == 2


def test_persist_advances_the_session_version_reference(service):
    session_state: dict = {}
    loaded = get_active_quotation(session_state, service)

    persist_workflow_state(session_state, loaded.state, service)
    assert session_state[ACTIVE_QUOTATION_VERSION_KEY] == 2

    persist_workflow_state(session_state, loaded.state, service)
    assert session_state[ACTIVE_QUOTATION_VERSION_KEY] == 3


def test_a_stale_session_cannot_silently_overwrite_another_user(service):
    first_session: dict = {}
    first = get_active_quotation(first_session, service)

    # A second user opens the same quotation in their own session.
    second_session = {ACTIVE_QUOTATION_KEY: first.quotation_id}
    second = get_active_quotation(second_session, service)

    second.state.draft.customer_name = "Second writer"
    persist_workflow_state(second_session, second.state, service)

    first.state.draft.customer_name = "First writer"
    with pytest.raises(QuotationVersionConflictError):
        persist_workflow_state(first_session, first.state, service)

    assert service.load_quotation(first.quotation_id).record.customer_name == (
        "Second writer"
    )


def test_a_dangling_session_reference_is_replaced(service):
    session_state = {ACTIVE_QUOTATION_KEY: "Q-GONE", ACTIVE_QUOTATION_VERSION_KEY: 9}

    loaded = get_active_quotation(session_state, service)

    assert loaded.quotation_id != "Q-GONE"
    assert session_state[ACTIVE_QUOTATION_KEY] == loaded.quotation_id


def test_starting_a_new_quotation_switches_the_reference(service):
    session_state: dict = {}
    first = get_active_quotation(session_state, service)

    second = start_new_quotation(session_state, service)

    assert second.quotation_id != first.quotation_id
    assert session_state[ACTIVE_QUOTATION_KEY] == second.quotation_id
    # The earlier quotation is still recoverable from the database.
    assert service.load_quotation(first.quotation_id) is not None


def test_a_previous_quotation_can_be_reopened_in_the_session(service):
    session_state: dict = {}
    first = get_active_quotation(session_state, service)
    first.state.draft.customer_name = "Synthetic Clinic"
    persist_workflow_state(session_state, first.state, service)
    start_new_quotation(session_state, service)

    reopened = open_quotation(session_state, first.quotation_id, service)

    assert reopened.state.draft.customer_name == "Synthetic Clinic"
    assert read_session_reference(session_state).quotation_id == first.quotation_id


def test_persisting_without_an_active_quotation_is_refused(service):
    from app.repositories.interfaces import QuotationNotFoundError

    with pytest.raises(QuotationNotFoundError):
        persist_workflow_state({}, QuotationWorkflowState.__new__(
            QuotationWorkflowState
        ), service)
