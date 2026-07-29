from __future__ import annotations

from email.message import EmailMessage

import pytest

from anlass.models import ReplyKind
from anlass.track import TrackError
from anlass.track.imap import ImapTracker

USERNAME_ENV = "ANLASS_TEST_IMAP_USER"
PASSWORD_ENV = "ANLASS_TEST_IMAP_PASSWORD"


class FakeImapClient:
    """Stands in for ``imaplib.IMAP4_SSL``. No test in this file opens a socket."""

    def __init__(self, messages: dict[bytes, bytes], fail_fetch_for: bytes | None = None) -> None:
        self.messages = messages
        self.fail_fetch_for = fail_fetch_for
        self.logged_in: tuple[str, str] | None = None
        self.selected: str | None = None
        self.logged_out = False

    def login(self, username: str, password: str):
        self.logged_in = (username, password)
        return "OK", [b"done"]

    def select(self, mailbox: str):
        self.selected = mailbox
        return "OK", [b"1"]

    def search(self, charset, criteria):
        return "OK", [b" ".join(self.messages.keys())]

    def fetch(self, msg_id: bytes, parts: str):
        if msg_id == self.fail_fetch_for:
            raise RuntimeError("Verbindung abgebrochen")
        raw = self.messages.get(msg_id)
        if raw is None:
            return "NO", []
        return "OK", [(b"%s (RFC822 {%d}" % (msg_id, len(raw)), raw)]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"logout"]


def _raw_email(
    *,
    message_id: str,
    subject: str,
    from_addr: str,
    body: str,
    in_reply_to: str | None = None,
    auto_submitted: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["Message-ID"] = message_id
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = "mara@beispiel.example"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if auto_submitted:
        message["Auto-Submitted"] = auto_submitted
    message.set_content(body)
    return message.as_bytes()


def _tracker(client: FakeImapClient) -> ImapTracker:
    return ImapTracker(
        host="imap.beispiel.example",
        username_env=USERNAME_ENV,
        password_env=PASSWORD_ENV,
        client_factory=lambda host, port: client,
    )


def test_poll_fails_loudly_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(USERNAME_ENV, raising=False)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    tracker = _tracker(FakeImapClient({}))

    with pytest.raises(TrackError):
        tracker.poll()


def test_poll_correlates_a_reply_by_in_reply_to(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    raw = _raw_email(
        message_id="<reply-1@nordlicht.example>",
        subject="Re: Ihre Ausschreibung",
        from_addr="Frau Roth <roth@nordlicht.example>",
        body="Das klingt interessant, lassen Sie uns gerne kennenlernen.",
        in_reply_to="<sent-1@beispiel.example>",
    )
    client = FakeImapClient({b"1": raw})
    tracker = _tracker(client)
    tracker.register_sent("<sent-1@beispiel.example>", "draft_42", subject="Ihre Ausschreibung")

    replies = tracker.poll()

    assert client.logged_in == ("mara", "geheim")
    assert len(replies) == 1
    reply = replies[0]
    assert reply.draft_id == "draft_42"
    assert reply.sender == "roth@nordlicht.example"
    assert reply.kind == ReplyKind.INTERESTED


def test_poll_falls_back_to_subject_when_headers_do_not_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    raw = _raw_email(
        message_id="<reply-2@nordlicht.example>",
        subject="AW: Ihre Ausschreibung",
        from_addr="roth@nordlicht.example",
        body="Danke fuer Ihre Nachricht.",
        in_reply_to="<unregistered@irgendwo.example>",
    )
    client = FakeImapClient({b"1": raw})
    tracker = _tracker(client)
    tracker.register_sent("<sent-1@beispiel.example>", "draft_42", subject="Ihre Ausschreibung")

    replies = tracker.poll()

    assert replies[0].draft_id == "draft_42"


def test_poll_leaves_draft_id_none_when_nothing_correlates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    raw = _raw_email(
        message_id="<reply-3@nordlicht.example>",
        subject="Ganz anderes Thema",
        from_addr="roth@nordlicht.example",
        body="Hallo.",
    )
    client = FakeImapClient({b"1": raw})
    tracker = _tracker(client)

    replies = tracker.poll()

    assert replies[0].draft_id is None


def test_auto_submitted_header_wins_over_a_matching_interest_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Header detection happens before the text classifier ever runs."""
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    raw = _raw_email(
        message_id="<reply-4@nordlicht.example>",
        subject="Automatische Antwort",
        from_addr="roth@nordlicht.example",
        body="Vielen Dank, das klingt interessant, wir melden uns.",
        auto_submitted="auto-replied",
    )
    client = FakeImapClient({b"1": raw})
    tracker = _tracker(client)

    replies = tracker.poll()

    assert replies[0].kind == ReplyKind.AUTO_REPLY


def test_poll_respects_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    messages = {
        f"{i}".encode(): _raw_email(
            message_id=f"<reply-{i}@nordlicht.example>", subject="Kontakt", from_addr="roth@nordlicht.example",
            body="Hallo.",
        )
        for i in range(1, 4)
    }
    client = FakeImapClient(messages)
    tracker = _tracker(client)

    replies = tracker.poll(limit=2)

    assert len(replies) == 2


def test_poll_logs_out_even_when_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USERNAME_ENV, "mara")
    monkeypatch.setenv(PASSWORD_ENV, "geheim")
    client = FakeImapClient({b"1": b""}, fail_fetch_for=b"1")
    tracker = _tracker(client)

    with pytest.raises(TrackError):
        tracker.poll()

    assert client.logged_out is True
