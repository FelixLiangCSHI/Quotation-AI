"""Recipient resolution and validation.

Internal addresses are resolved from stored, authenticated ``User`` records.
A caller can never substitute an arbitrary external address for the assigned
approver, and customer addresses are accepted only for customer-approved
outputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.emailing.config import EmailConfig
from app.emailing.contracts import (
    CUSTOMER_FACING_TYPES,
    EmailAudience,
    EmailType,
    RecipientValidationError,
)

#: Deliberately conservative. Anything unusual is refused rather than sent.
ADDRESS_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$"
)

MAX_ADDRESS_LENGTH = 320


@dataclass(frozen=True)
class ResolvedRecipients:
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()


def normalise_address(value: str) -> str:
    return (value or "").strip()


def is_valid_address(value: str) -> bool:
    candidate = normalise_address(value)
    if not candidate or len(candidate) > MAX_ADDRESS_LENGTH:
        return False
    return bool(ADDRESS_PATTERN.match(candidate))


def address_domain(value: str) -> str:
    candidate = normalise_address(value)
    _, _, domain = candidate.rpartition("@")
    return domain.casefold()


def is_internal_address(value: str, config: EmailConfig) -> bool:
    """Return whether ``value`` belongs to a configured internal domain.

    When no internal domain is configured (local development), every valid
    address is treated as internal so the console provider stays usable.
    """

    if not config.internal_domains:
        return True
    return address_domain(value) in config.internal_domains


def validate_recipients(
    recipients: tuple[str, ...],
    *,
    email_type: EmailType,
    audience: EmailAudience,
    config: EmailConfig,
    field_name: str = "recipients",
) -> tuple[str, ...]:
    """Validate a recipient list for one email type.

    Raises :class:`RecipientValidationError` on an empty list, a malformed
    address, an external address on an internal email, or any customer
    address on an email type that is not customer-facing.
    """

    cleaned = tuple(
        normalise_address(value) for value in recipients if normalise_address(value)
    )
    if not cleaned:
        raise RecipientValidationError(
            f"At least one {field_name.replace('_', ' ')} is required."
        )
    deduplicated: list[str] = []
    for address in cleaned:
        if not is_valid_address(address):
            raise RecipientValidationError(
                f"{address!r} is not a syntactically valid email address."
            )
        if address.casefold() not in {item.casefold() for item in deduplicated}:
            deduplicated.append(address)

    if audience is EmailAudience.CUSTOMER:
        if email_type not in CUSTOMER_FACING_TYPES:
            raise RecipientValidationError(
                f"{email_type.value} is an internal email and must not be "
                "addressed to a customer."
            )
        if not config.allow_customer_delivery:
            raise RecipientValidationError(
                "Customer email delivery is disabled by configuration."
            )
    else:
        external = [
            address
            for address in deduplicated
            if not is_internal_address(address, config)
        ]
        if external:
            raise RecipientValidationError(
                "Internal emails may only be addressed to an internal domain: "
                + ", ".join(sorted(external))
            )
    return tuple(deduplicated)


def resolve_user_address(user, *, config: EmailConfig, role_label: str) -> str:
    """Resolve one internal address from a stored user record.

    ``user`` is a :class:`app.domain.dto.UserDTO`. The stored address is the
    only accepted source; a caller-supplied address is never used.
    """

    if user is None:
        raise RecipientValidationError(
            f"The {role_label} is not a known internal user."
        )
    address = normalise_address(getattr(user, "email", ""))
    if not address:
        raise RecipientValidationError(
            f"The {role_label} ({getattr(user, 'username', '')}) has no stored "
            "email address. Ask an administrator to record one."
        )
    if not is_valid_address(address):
        raise RecipientValidationError(
            f"The stored address for the {role_label} is not a valid email "
            "address."
        )
    if not is_internal_address(address, config):
        raise RecipientValidationError(
            f"The stored address for the {role_label} is not on an internal "
            "domain."
        )
    return address
