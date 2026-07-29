"""Real delivery over SMTP.

Credentials are read from the environment at the moment of sending, never stored on
the instance and never written to a log or an exception message - the same discipline
as :mod:`anlass.llm.apikey`. The connection itself is built by ``client_factory`` so
tests can inject a fake client; no test in this package opens a real socket.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Callable, ContextManager

from ..models import OutboundMessage, TransportReceipt
from . import TransportError
from .preflight import DeliverabilityCheck

__all__ = ["SmtpTransport"]


def _default_client(host: str, port: int, timeout: float) -> ContextManager:
    return smtplib.SMTP(host, port, timeout=timeout)


@dataclass
class SmtpTransport:
    """SMTP transport with STARTTLS.

    Args:
        host: SMTP server.
        port: SMTP port.
        use_tls: Whether to call ``STARTTLS`` after connecting.
        sender: Fallback ``From`` if ``message.sender`` is not set.
        username_env: Environment variable holding the SMTP username.
        password_env: Environment variable holding the SMTP password.
        deliverability: The preflight check run before sending.
        bulk: Whether this send counts as a series send for the preflight check.
        sent_today: How many messages already went out over this mailbox today - the
            caller tracks this; the transport has no memory of past sends.
        client_factory: Builds the connection. Defaults to a real ``smtplib.SMTP``.
    """

    host: str
    port: int = 587
    use_tls: bool = True
    sender: str | None = None
    username_env: str = "ANLASS_SMTP_USER"
    password_env: str = "ANLASS_SMTP_PASSWORD"
    deliverability: DeliverabilityCheck = field(default_factory=DeliverabilityCheck)
    bulk: bool = True
    sent_today: int = 0
    timeout: float = 30.0
    client_factory: Callable[[str, int, float], ContextManager] = field(default=_default_client)

    @property
    def name(self) -> str:
        return "smtp"

    def preflight(self, message: OutboundMessage) -> list[str]:
        """SPF/DKIM/DMARC, unsubscribe and volume - see :mod:`anlass.transport.preflight`."""
        return self.deliverability.check(message, bulk=self.bulk, sent_today=self.sent_today)

    def send(self, message: OutboundMessage) -> TransportReceipt:
        username = os.environ.get(self.username_env, "").strip()
        password = os.environ.get(self.password_env, "")
        if not username or not password:
            raise TransportError(
                f"SMTP-Zugangsdaten fehlen in der Umgebung ({self.username_env}/{self.password_env})."
            )

        email_message = self._build_email(message)
        try:
            with self.client_factory(self.host, self.port, self.timeout) as client:
                if self.use_tls:
                    client.starttls()
                client.login(username, password)
                client.send_message(email_message)
        except Exception as exc:  # noqa: BLE001 - never let this leak the credentials in the message
            raise TransportError(f"SMTP-Versand fehlgeschlagen ({type(exc).__name__}).") from exc

        return TransportReceipt(
            transport=self.name,
            accepted=True,
            reference=str(email_message["Message-ID"]),
            detail="",
        )

    def _build_email(self, message: OutboundMessage) -> EmailMessage:
        email_message = EmailMessage()
        email_message["Subject"] = message.subject
        email_message["To"] = message.recipient
        email_message["From"] = message.sender or self.sender or ""
        email_message["Message-ID"] = make_msgid()
        for key, value in message.headers.items():
            email_message[key] = value
        email_message.set_content(message.body)
        return email_message
