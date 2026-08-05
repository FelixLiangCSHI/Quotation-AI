"""Password hashing for locally managed internal accounts.

Uses PBKDF2-HMAC-SHA256 from the standard library so no new dependency is
introduced. A stored hash contains only the algorithm, iteration count, salt
and derived key; the password itself is never stored or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from base64 import b64decode, b64encode

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 240_000
SALT_BYTES = 16
MINIMUM_PASSWORD_LENGTH = 8


class WeakPasswordError(ValueError):
    """Raised when a proposed password does not meet the minimum policy."""


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    allow_weak: bool = False,
) -> str:
    """Return an encoded hash of ``password``.

    ``allow_weak`` skips the minimum-length policy. It exists only for the
    seeded demo accounts of a synthetic demo deployment and must never be used
    for a real account.
    """

    if password is None or (
        not allow_weak and len(password) < MINIMUM_PASSWORD_LENGTH
    ):
        raise WeakPasswordError(
            "The password must be at least "
            f"{MINIMUM_PASSWORD_LENGTH} characters long."
        )
    if not password:
        raise WeakPasswordError("A password is required.")
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return "$".join(
        (
            ALGORITHM,
            str(iterations),
            b64encode(salt).decode("ascii"),
            b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Constant-time check of ``password`` against ``encoded_hash``."""

    if not password or not encoded_hash:
        return False
    try:
        algorithm, iteration_text, salt_text, key_text = encoded_hash.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iteration_text)
        salt = b64decode(salt_text.encode("ascii"))
        expected = b64decode(key_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)
