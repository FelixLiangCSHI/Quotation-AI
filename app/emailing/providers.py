"""Email delivery adapters.

Each adapter implements :class:`~app.emailing.contracts.EmailDeliveryProvider`.
No adapter contains a credential; secrets are resolved from the environment at
call time through :mod:`app.emailing.config`.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Callable, Mapping

from app.emailing.config import EmailConfig, GraphSettings, SMTPSettings
from app.emailing.contracts import (
    DeliveryErrorCategory,
    EmailDeliveryResult,
    OutboundEmail,
    PermanentDeliveryError,
    ProviderHealth,
    TransientDeliveryError,
)


def build_mime_message(message: OutboundEmail) -> EmailMessage:
    """Render an :class:`OutboundEmail` as a MIME message."""

    mime = EmailMessage()
    mime["From"] = message.sender
    mime["To"] = ", ".join(message.recipients)
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)
    mime["Subject"] = message.subject
    mime.set_content(message.body)
    for attachment in message.attachments:
        maintype, _, subtype = attachment.mime_type.partition("/")
        mime.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return mime


class ConsoleEmailProvider:
    """Local development and test provider.

    Nothing leaves the process. Messages are captured in memory and, when a
    sink is supplied, echoed to it. Repeated sends with the same idempotency
    key are reported as deduplicated rather than delivered twice.
    """

    provider_name = "console"

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink
        self.sent: list[tuple[str, OutboundEmail]] = []
        self._keys: set[str] = set()

    def send(
        self, *, message: OutboundEmail, idempotency_key: str
    ) -> EmailDeliveryResult:
        if idempotency_key and idempotency_key in self._keys:
            return EmailDeliveryResult(
                delivered=True,
                provider=self.provider_name,
                provider_message_id=idempotency_key,
                idempotency_key=idempotency_key,
                deduplicated=True,
            )
        self._keys.add(idempotency_key)
        self.sent.append((idempotency_key, message))
        if self._sink is not None:
            self._sink(
                f"To: {', '.join(message.recipients)}\n"
                f"Subject: {message.subject}\n\n{message.body}"
            )
        return EmailDeliveryResult(
            delivered=True,
            provider=self.provider_name,
            provider_message_id=idempotency_key,
            idempotency_key=idempotency_key,
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=True,
            detail="Console provider writes locally and sends nothing.",
        )


class SMTPEmailProvider:
    """SMTP delivery. Credentials come from the environment only."""

    provider_name = "smtp"

    def __init__(
        self,
        settings: SMTPSettings,
        *,
        environment: Mapping[str, str] | None = None,
        client_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        self._settings = settings
        self._environment = environment
        self._client_factory = client_factory or smtplib.SMTP

    def send(
        self, *, message: OutboundEmail, idempotency_key: str
    ) -> EmailDeliveryResult:
        if not self._settings.configured:
            raise PermanentDeliveryError(
                "SMTP_HOST is not configured.",
                category=DeliveryErrorCategory.CONFIGURATION,
            )
        mime = build_mime_message(message)
        recipients = list(message.recipients) + list(message.cc) + list(
            message.bcc
        )
        try:
            client = self._client_factory(
                self._settings.host,
                self._settings.port,
                timeout=self._settings.timeout_seconds,
            )
            try:
                if self._settings.use_tls:
                    client.starttls()
                password = self._settings.resolve_password(self._environment)
                if self._settings.username and password:
                    client.login(self._settings.username, password)
                client.send_message(
                    mime, from_addr=message.sender, to_addrs=recipients
                )
            finally:
                client.quit()
        except smtplib.SMTPAuthenticationError as error:
            raise PermanentDeliveryError(
                "SMTP authentication was refused.",
                category=DeliveryErrorCategory.AUTHENTICATION,
            ) from error
        except smtplib.SMTPRecipientsRefused as error:
            raise PermanentDeliveryError(
                "Every SMTP recipient was refused.",
                category=DeliveryErrorCategory.RECIPIENT,
            ) from error
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError) as error:
            raise TransientDeliveryError(
                f"SMTP delivery failed transiently: {type(error).__name__}"
            ) from error
        except smtplib.SMTPException as error:
            raise PermanentDeliveryError(
                f"SMTP delivery failed: {type(error).__name__}"
            ) from error
        return EmailDeliveryResult(
            delivered=True,
            provider=self.provider_name,
            provider_message_id=idempotency_key,
            idempotency_key=idempotency_key,
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=self._settings.configured,
            detail=(
                "SMTP host configured."
                if self._settings.configured
                else "SMTP_HOST is not set."
            ),
        )


class GraphTransport:
    """Transport slot for Microsoft Graph.

    The real HTTP client is injected by the deployment that owns the company
    credentials. Nothing here performs a network call by default.
    """

    def send_mail(
        self,
        *,
        base_url: str,
        sender_user_id: str,
        payload: dict,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> str:
        raise PermanentDeliveryError(
            "No Microsoft Graph transport is installed in this deployment.",
            category=DeliveryErrorCategory.CONFIGURATION,
        )


class MicrosoftGraphEmailProvider:
    """Microsoft Graph adapter.

    The interface and validation are complete. Delivery stays configuration
    gated: without ``GRAPH_ENABLED`` and the credential environment variables
    it refuses with a permanent configuration error instead of sending.
    """

    provider_name = "microsoft_graph"

    def __init__(
        self,
        settings: GraphSettings,
        *,
        environment: Mapping[str, str] | None = None,
        transport: GraphTransport | None = None,
    ) -> None:
        self._settings = settings
        self._environment = environment
        self._transport = transport or GraphTransport()

    def build_payload(self, message: OutboundEmail) -> dict:
        """Return the Graph ``sendMail`` payload for ``message``."""

        def _recipients(addresses: tuple[str, ...]) -> list[dict]:
            return [
                {"emailAddress": {"address": address}} for address in addresses
            ]

        payload: dict = {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "Text", "content": message.body},
                "toRecipients": _recipients(message.recipients),
                "ccRecipients": _recipients(message.cc),
                "bccRecipients": _recipients(message.bcc),
            },
            "saveToSentItems": True,
        }
        if message.attachments:
            import base64

            payload["message"]["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": attachment.filename,
                    "contentType": attachment.mime_type,
                    "contentBytes": base64.b64encode(
                        attachment.content
                    ).decode("ascii"),
                }
                for attachment in message.attachments
            ]
        return payload

    def send(
        self, *, message: OutboundEmail, idempotency_key: str
    ) -> EmailDeliveryResult:
        if not self._settings.enabled:
            raise PermanentDeliveryError(
                "Microsoft Graph delivery is disabled (GRAPH_ENABLED).",
                category=DeliveryErrorCategory.CONFIGURATION,
            )
        missing = self._settings.missing_settings(self._environment)
        if missing:
            raise PermanentDeliveryError(
                "Microsoft Graph configuration is incomplete: "
                + ", ".join(missing),
                category=DeliveryErrorCategory.CONFIGURATION,
            )
        message_id = self._transport.send_mail(
            base_url=self._settings.base_url,
            sender_user_id=self._settings.sender_user_id,
            payload=self.build_payload(message),
            tenant_id=self._settings.resolve(
                self._settings.tenant_id_env, self._environment
            ),
            client_id=self._settings.resolve(
                self._settings.client_id_env, self._environment
            ),
            client_secret=self._settings.resolve(
                self._settings.client_secret_env, self._environment
            ),
        )
        return EmailDeliveryResult(
            delivered=True,
            provider=self.provider_name,
            provider_message_id=message_id or idempotency_key,
            idempotency_key=idempotency_key,
        )

    def health_check(self) -> ProviderHealth:
        missing = self._settings.missing_settings(self._environment)
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=self._settings.enabled and not missing,
            detail=(
                "Microsoft Graph is configured."
                if self._settings.enabled and not missing
                else "Microsoft Graph is configuration gated. Missing: "
                + (", ".join(missing) or "GRAPH_ENABLED")
            ),
        )


def build_delivery_provider(
    config: EmailConfig,
    *,
    environment: Mapping[str, str] | None = None,
):
    """Build the configured delivery provider."""

    if config.provider == "smtp":
        return SMTPEmailProvider(config.smtp, environment=environment)
    if config.provider == "microsoft_graph":
        return MicrosoftGraphEmailProvider(config.graph, environment=environment)
    return ConsoleEmailProvider()
