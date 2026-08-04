"""UI session references.

Streamlit session state must hold only references and transient UI values.
Trusted quotation state lives in the database and is loaded through the
service layer on every interaction.

This module is the single place that reads or writes those references, so no
business logic depends on Streamlit.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

#: Session key holding the active quotation's stable business identifier.
ACTIVE_QUOTATION_KEY = "active_quotation_id"
#: Session key holding the version the UI last rendered, used to detect a
#: concurrent edit before writing.
ACTIVE_QUOTATION_VERSION_KEY = "active_quotation_version"
#: Session key holding the signed-in user id. Populated in a later phase.
ACTIVE_USER_KEY = "active_user_id"

SESSION_REFERENCE_KEYS = (
    ACTIVE_QUOTATION_KEY,
    ACTIVE_QUOTATION_VERSION_KEY,
    ACTIVE_USER_KEY,
)


@dataclass(frozen=True)
class SessionReference:
    """The identifiers the UI needs to reload trusted state."""

    quotation_id: str | None = None
    quotation_version: int | None = None
    user_id: int | None = None


def read_session_reference(
    session_state: MutableMapping[str, Any],
) -> SessionReference:
    return SessionReference(
        quotation_id=session_state.get(ACTIVE_QUOTATION_KEY),
        quotation_version=session_state.get(ACTIVE_QUOTATION_VERSION_KEY),
        user_id=session_state.get(ACTIVE_USER_KEY),
    )


def set_active_quotation(
    session_state: MutableMapping[str, Any],
    *,
    quotation_id: str,
    version: int,
) -> None:
    session_state[ACTIVE_QUOTATION_KEY] = quotation_id
    session_state[ACTIVE_QUOTATION_VERSION_KEY] = version


def set_active_user(
    session_state: MutableMapping[str, Any],
    user_id: int | None,
) -> None:
    session_state[ACTIVE_USER_KEY] = user_id


def clear_active_quotation(session_state: MutableMapping[str, Any]) -> None:
    session_state.pop(ACTIVE_QUOTATION_KEY, None)
    session_state.pop(ACTIVE_QUOTATION_VERSION_KEY, None)
