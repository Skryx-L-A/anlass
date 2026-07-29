from __future__ import annotations

import pytest

from anlass.models import OutboundMessage
from anlass.transport import TransportError
from anlass.transport.preflight import DeliverabilityCheck
from anlass.transport.smtp import SmtpTransport

USERNAME_ENV = "ANLASS_TEST_SMTP_USER"
PASSWORD_ENV = "ANLASS_TEST_SMTP_PASSWORD"
SECRET_PASSWORD = "hunter2-correct-horse"


class FakeSmtpClient:
    """Stands in for ``smtplib.SMTP``. No test in this file opens a socket."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.started_tls = False
        self.logged_in_as: tuple[str, str] | None = None
        self.sent = []

    def __enter__(self) -> "FakeSmtpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("535 Authentication failed")
        self.logged_in_as = (username, password)

    def send_message(self, message) -> None:
        self.sent.append(message)


def _message(**overrides) -> OutboundMessage:
    fields = dict(
        draft_id="draft_test",
        recipient="roth@nordlicht.example",
        subject="Ihre Ausschreibung",
        body="Sehr geehrte Frau Roth, ...",
        sender="mara@beispiel.example",
    )
    fields.update(overrides)
    return OutboundMessage(**fields)


def test_send_fails_loudly_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(USERNAME_ENV, raising=False)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    transport = SmtpTransport(host="mail.beispiel.example", username_env=USERNAME_ENV, password_env=PASSWORD_ENV)

    with pytest.raises(TransportError):
        transport.send(_message())


def test_send_reads_credentials_only_from_the_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, SECRET_PASSWORD)
    client = FakeSmtpClient()
    transport = SmtpTransport(
        host="mail.beispiel.example",
        username_env=USERNAME_ENV,
        password_env=PASSWORD_ENV,
        client_factory=lambda host, port, timeout: client,
    )

    receipt = transport.send(_message())

    assert receipt.transport == "smtp"
    assert receipt.accepted is True
    assert receipt.reference  # a Message-ID was assigned
    assert client.started_tls is True
    assert client.logged_in_as == ("mara", SECRET_PASSWORD)
    assert len(client.sent) == 1


def test_credentials_never_stored_on_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, SECRET_PASSWORD)
    client = FakeSmtpClient()
    transport = SmtpTransport(
        host="mail.beispiel.example",
        username_env=USERNAME_ENV,
        password_env=PASSWORD_ENV,
        client_factory=lambda host, port, timeout: client,
    )
    transport.send(_message())

    assert SECRET_PASSWORD not in repr(transport)
    assert SECRET_PASSWORD not in vars(transport).values()


def test_failed_delivery_raises_without_leaking_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, SECRET_PASSWORD)
    client = FakeSmtpClient(fail=True)
    transport = SmtpTransport(
        host="mail.beispiel.example",
        username_env=USERNAME_ENV,
        password_env=PASSWORD_ENV,
        client_factory=lambda host, port, timeout: client,
    )

    with pytest.raises(TransportError) as excinfo:
        transport.send(_message())

    assert SECRET_PASSWORD not in str(excinfo.value)


def test_preflight_reports_missing_records_and_missing_unsubscribe() -> None:
    check = DeliverabilityCheck(resolver=lambda name: [])
    transport = SmtpTransport(host="mail.beispiel.example", deliverability=check, bulk=True)

    problems = transport.preflight(_message())

    joined = " ".join(problems)
    assert "SPF" in joined
    assert "DKIM" in joined
    assert "DMARC" in joined
    assert "Abmeldemoeglichkeit" in joined


def test_preflight_clean_when_everything_checks_out() -> None:
    def resolver(name: str) -> list[str]:
        if name == "beispiel.example":
            return ["v=spf1 include:_spf.beispiel.example ~all"]
        if name == "_dmarc.beispiel.example":
            return ["v=DMARC1; p=reject"]
        if name.startswith("default._domainkey."):
            return ["v=DKIM1; k=rsa; p=abc"]
        return []

    check = DeliverabilityCheck(resolver=resolver)
    transport = SmtpTransport(
        host="mail.beispiel.example",
        deliverability=check,
        bulk=True,
    )
    message = _message(headers={"List-Unsubscribe": "<mailto:abmelden@beispiel.example>"})

    assert transport.preflight(message) == []
