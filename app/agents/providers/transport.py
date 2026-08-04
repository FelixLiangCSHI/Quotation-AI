"""Transport used by the HTTP based providers.

The domain layer never imports this module and no provider SDK is used. The
default transport is a thin :mod:`urllib` wrapper so the transport can be
replaced by a fake in tests.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Mapping, Protocol
from urllib.parse import urlparse

from app.agents.contracts import (
    AgentProviderConfigurationError,
    AgentProviderError,
    AgentProviderTimeout,
)

ALLOWED_SCHEMES = frozenset({"https", "http"})


class HttpTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        payload: dict,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> dict:
        """POST ``payload`` as JSON and return the decoded JSON response."""


def validate_base_url(base_url: str | None) -> str:
    if not base_url:
        raise AgentProviderConfigurationError("Base URL is not configured.")
    parsed = urlparse(base_url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise AgentProviderConfigurationError(
            "Base URL must be an absolute http(s) URL."
        )
    return base_url.rstrip("/")


class UrllibTransport:
    """Minimal JSON-over-HTTP transport with no third-party dependency."""

    def post_json(
        self,
        *,
        url: str,
        payload: dict,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> dict:
        validate_base_url(url)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - scheme validated above
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **dict(headers)},
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - scheme validated above
                request, timeout=timeout_seconds
            ) as response:
                raw = response.read().decode("utf-8")
        except socket.timeout as error:
            raise AgentProviderTimeout("Provider request timed out.") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, socket.timeout):
                raise AgentProviderTimeout(
                    "Provider request timed out."
                ) from error
            raise AgentProviderError("Provider request failed.") from error
        except OSError as error:
            raise AgentProviderError("Provider request failed.") from error
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AgentProviderError(
                "Provider returned a non-JSON envelope."
            ) from error
        if not isinstance(decoded, dict):
            raise AgentProviderError("Provider envelope must be a JSON object.")
        return decoded
