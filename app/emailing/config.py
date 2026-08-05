"""Email configuration read from environment variables only.

No secret value is ever stored in this module or in the configuration object.
SMTP passwords and Microsoft Graph client secrets are resolved at call time
from the environment variable *name* recorded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})

SUPPORTED_PROVIDERS = frozenset({"console", "smtp", "microsoft_graph"})

DEFAULT_PROVIDER = "console"
DEFAULT_REMINDER_DELAY_HOURS = 48.0
DEFAULT_MAX_REMINDERS = 1
DEFAULT_MAX_DELIVERY_ATTEMPTS = 3
DEFAULT_BODY_STORAGE = "hash"

BODY_STORAGE_MODES = frozenset({"full", "redacted", "hash"})

#: Environment variable names. Listed so the UI can show configuration
#: without ever reading a secret value.
ENVIRONMENT_VARIABLES = (
    "EMAIL_DELIVERY_PROVIDER",
    "EMAIL_SENDER_ADDRESS",
    "EMAIL_INTERNAL_DOMAINS",
    "EMAIL_ALLOW_CUSTOMER_DELIVERY",
    "EMAIL_AUTO_SEND_APPROVAL_REQUEST",
    "EMAIL_BODY_STORAGE",
    "EMAIL_MAX_DELIVERY_ATTEMPTS",
    "EMAIL_TEMPLATE_VERSION",
    "APPROVAL_REMINDER_DELAY_HOURS",
    "APPROVAL_REMINDER_MAX_COUNT",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD_ENV",
    "SMTP_USE_TLS",
    "SMTP_TIMEOUT_SECONDS",
    "GRAPH_TENANT_ID_ENV",
    "GRAPH_CLIENT_ID_ENV",
    "GRAPH_CLIENT_SECRET_ENV",
    "GRAPH_SENDER_USER_ID",
    "GRAPH_BASE_URL",
    "GRAPH_ENABLED",
)


class EmailConfigurationError(ValueError):
    """Raised when the email environment configuration cannot be used."""


def _clean(value: str | None) -> str:
    return "" if value is None else value.strip()


def _flag(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().casefold()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise EmailConfigurationError(f"{name} must be a boolean value")


def _float(name: str, raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise EmailConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise EmailConfigurationError(f"{name} must be greater than zero")
    return value


def _int(name: str, raw: str | None, default: int, *, minimum: int = 1) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise EmailConfigurationError(f"{name} must be an integer") from error
    if value < minimum:
        raise EmailConfigurationError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class SMTPSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    password_env: str = "SMTP_PASSWORD"
    use_tls: bool = True
    timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.host)

    def resolve_password(
        self, environment: Mapping[str, str] | None = None
    ) -> str:
        """Resolve the password at call time. Never cached, never logged."""

        values = os.environ if environment is None else environment
        return _clean(values.get(self.password_env))

    def describe(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password_env": self.password_env,
            "password_present": bool(self.resolve_password()),
            "use_tls": self.use_tls,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class GraphSettings:
    enabled: bool = False
    tenant_id_env: str = "GRAPH_TENANT_ID"
    client_id_env: str = "GRAPH_CLIENT_ID"
    client_secret_env: str = "GRAPH_CLIENT_SECRET"
    sender_user_id: str = ""
    base_url: str = "https://graph.microsoft.com/v1.0"

    def resolve(
        self, name: str, environment: Mapping[str, str] | None = None
    ) -> str:
        values = os.environ if environment is None else environment
        return _clean(values.get(name))

    def missing_settings(
        self, environment: Mapping[str, str] | None = None
    ) -> tuple[str, ...]:
        """Return the names of the settings that are still absent."""

        missing = []
        for variable in (
            self.tenant_id_env,
            self.client_id_env,
            self.client_secret_env,
        ):
            if not self.resolve(variable, environment):
                missing.append(variable)
        if not self.sender_user_id:
            missing.append("GRAPH_SENDER_USER_ID")
        return tuple(missing)

    @property
    def configured(self) -> bool:
        return self.enabled and not self.missing_settings()

    def describe(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "tenant_id_env": self.tenant_id_env,
            "client_id_env": self.client_id_env,
            "client_secret_env": self.client_secret_env,
            "sender_user_id": self.sender_user_id,
            "base_url": self.base_url,
            "missing_settings": list(self.missing_settings()),
        }


@dataclass(frozen=True)
class EmailConfig:
    provider: str = DEFAULT_PROVIDER
    sender_address: str = "quotation-bot@example.invalid"
    internal_domains: tuple[str, ...] = ()
    allow_customer_delivery: bool = True
    auto_send_approval_request: bool = False
    body_storage: str = DEFAULT_BODY_STORAGE
    max_delivery_attempts: int = DEFAULT_MAX_DELIVERY_ATTEMPTS
    template_version: str = "v1"
    reminder_delay_hours: float = DEFAULT_REMINDER_DELAY_HOURS
    reminder_max_count: int = DEFAULT_MAX_REMINDERS
    smtp: SMTPSettings = SMTPSettings()
    graph: GraphSettings = GraphSettings()

    def describe(self) -> dict[str, object]:
        """Secret-free description suitable for logs, UI and audit records."""

        return {
            "provider": self.provider,
            "sender_address": self.sender_address,
            "internal_domains": list(self.internal_domains),
            "allow_customer_delivery": self.allow_customer_delivery,
            "auto_send_approval_request": self.auto_send_approval_request,
            "body_storage": self.body_storage,
            "max_delivery_attempts": self.max_delivery_attempts,
            "template_version": self.template_version,
            "reminder_delay_hours": self.reminder_delay_hours,
            "reminder_max_count": self.reminder_max_count,
            "smtp": self.smtp.describe(),
            "graph": self.graph.describe(),
        }


def load_email_config(
    environment: Mapping[str, str] | None = None,
) -> EmailConfig:
    values = os.environ if environment is None else environment

    provider = (
        _clean(values.get("EMAIL_DELIVERY_PROVIDER")) or DEFAULT_PROVIDER
    ).casefold()
    if provider not in SUPPORTED_PROVIDERS:
        choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise EmailConfigurationError(
            f"EMAIL_DELIVERY_PROVIDER must be one of: {choices}"
        )

    body_storage = (
        _clean(values.get("EMAIL_BODY_STORAGE")) or DEFAULT_BODY_STORAGE
    ).casefold()
    if body_storage not in BODY_STORAGE_MODES:
        choices = ", ".join(sorted(BODY_STORAGE_MODES))
        raise EmailConfigurationError(
            f"EMAIL_BODY_STORAGE must be one of: {choices}"
        )

    domains = tuple(
        part.strip().casefold().lstrip("@")
        for part in _clean(values.get("EMAIL_INTERNAL_DOMAINS")).split(",")
        if part.strip()
    )

    return EmailConfig(
        provider=provider,
        sender_address=_clean(values.get("EMAIL_SENDER_ADDRESS"))
        or "quotation-bot@example.invalid",
        internal_domains=domains,
        allow_customer_delivery=_flag(
            "EMAIL_ALLOW_CUSTOMER_DELIVERY",
            values.get("EMAIL_ALLOW_CUSTOMER_DELIVERY"),
            True,
        ),
        auto_send_approval_request=_flag(
            "EMAIL_AUTO_SEND_APPROVAL_REQUEST",
            values.get("EMAIL_AUTO_SEND_APPROVAL_REQUEST"),
            False,
        ),
        body_storage=body_storage,
        max_delivery_attempts=_int(
            "EMAIL_MAX_DELIVERY_ATTEMPTS",
            values.get("EMAIL_MAX_DELIVERY_ATTEMPTS"),
            DEFAULT_MAX_DELIVERY_ATTEMPTS,
        ),
        template_version=_clean(values.get("EMAIL_TEMPLATE_VERSION")) or "v1",
        reminder_delay_hours=_float(
            "APPROVAL_REMINDER_DELAY_HOURS",
            values.get("APPROVAL_REMINDER_DELAY_HOURS"),
            DEFAULT_REMINDER_DELAY_HOURS,
        ),
        reminder_max_count=_int(
            "APPROVAL_REMINDER_MAX_COUNT",
            values.get("APPROVAL_REMINDER_MAX_COUNT"),
            DEFAULT_MAX_REMINDERS,
        ),
        smtp=SMTPSettings(
            host=_clean(values.get("SMTP_HOST")),
            port=_int("SMTP_PORT", values.get("SMTP_PORT"), 587),
            username=_clean(values.get("SMTP_USERNAME")),
            password_env=_clean(values.get("SMTP_PASSWORD_ENV"))
            or "SMTP_PASSWORD",
            use_tls=_flag("SMTP_USE_TLS", values.get("SMTP_USE_TLS"), True),
            timeout_seconds=_float(
                "SMTP_TIMEOUT_SECONDS", values.get("SMTP_TIMEOUT_SECONDS"), 30.0
            ),
        ),
        graph=GraphSettings(
            enabled=_flag("GRAPH_ENABLED", values.get("GRAPH_ENABLED"), False),
            tenant_id_env=_clean(values.get("GRAPH_TENANT_ID_ENV"))
            or "GRAPH_TENANT_ID",
            client_id_env=_clean(values.get("GRAPH_CLIENT_ID_ENV"))
            or "GRAPH_CLIENT_ID",
            client_secret_env=_clean(values.get("GRAPH_CLIENT_SECRET_ENV"))
            or "GRAPH_CLIENT_SECRET",
            sender_user_id=_clean(values.get("GRAPH_SENDER_USER_ID")),
            base_url=_clean(values.get("GRAPH_BASE_URL"))
            or "https://graph.microsoft.com/v1.0",
        ),
    )
