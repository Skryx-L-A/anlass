"""Fetch replies over IMAP and correlate each one to the draft it answers.

Correlation order: ``In-Reply-To``, then each token of ``References``, then the
subject with reply prefixes (``Re:``, ``AW:``, ``WG:``, ``Fwd:``) stripped - a fallback
only, because two unrelated threads can share a subject line and a header cannot lie
about which message it replies to the way a subject can. A caller must
:meth:`ImapTracker.register_sent` every sent message before polling, or every reply
comes back with ``draft_id=None`` - correlation data is not something IMAP hands out on
its own.

Credentials are read from the environment at the moment of connecting, same discipline
as every other transport in this package. The connection itself is built by
``client_factory``; no test in this package opens a real socket, and the fake client a
test injects only needs to answer ``login``, ``select``, ``search``, ``fetch`` and
``logout`` the way :mod:`imaplib` does.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from email import policy
from email.utils import parseaddr
from typing import Any, Callable

from ..models import Reply, ReplyKind, new_id
from . import TrackError
from .classify import ReplyClassifier

__all__ = ["ImapTracker"]

_REPLY_PREFIX_RE = re.compile(r"^(re|aw|wg|fwd?)\s*:\s*", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _default_client(host: str, port: int) -> imaplib.IMAP4_SSL:
    return imaplib.IMAP4_SSL(host, port)


def _normalize_message_id(token: str) -> str:
    return token.strip().strip("<>")


def _strip_reply_prefix(subject: str) -> str:
    text = subject.strip()
    while True:
        stripped = _REPLY_PREFIX_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.strip().lower()


@dataclass
class ImapTracker:
    """Polls one IMAP mailbox and classifies what it finds.

    Args:
        host: IMAP server.
        port: IMAP port, 993 for implicit TLS.
        mailbox: Folder to poll.
        username_env: Environment variable holding the IMAP username.
        password_env: Environment variable holding the IMAP password.
        classifier: Text-based classification, see :mod:`anlass.track.classify`.
        client_factory: Builds the connection. Defaults to a real ``IMAP4_SSL``.
    """

    host: str
    port: int = 993
    mailbox: str = "INBOX"
    username_env: str = "ANLASS_IMAP_USER"
    password_env: str = "ANLASS_IMAP_PASSWORD"
    classifier: ReplyClassifier = field(default_factory=ReplyClassifier)
    client_factory: Callable[[str, int], Any] = field(default=_default_client)
    _known_ids: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _known_subjects: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    @property
    def name(self) -> str:
        return "imap"

    def register_sent(self, message_id: str, draft_id: str, subject: str | None = None) -> None:
        """Remember a sent message so a later reply can be correlated to it."""
        self._known_ids[_normalize_message_id(message_id)] = draft_id
        if subject:
            self._known_subjects[_strip_reply_prefix(subject)] = draft_id

    def poll(self, *, since: datetime | None = None, limit: int | None = None) -> list[Reply]:
        username = os.environ.get(self.username_env, "").strip()
        password = os.environ.get(self.password_env, "")
        if not username or not password:
            raise TrackError(
                f"IMAP-Zugangsdaten fehlen in der Umgebung ({self.username_env}/{self.password_env})."
            )

        client = self.client_factory(self.host, self.port)
        try:
            typ, _data = client.login(username, password)
            if typ != "OK":
                raise TrackError("IMAP-Anmeldung fehlgeschlagen.")
            typ, _data = client.select(self.mailbox)
            if typ != "OK":
                raise TrackError(f"Postfach '{self.mailbox}' konnte nicht ausgewaehlt werden.")
            typ, data = client.search(None, self._search_criteria(since))
            if typ != "OK":
                raise TrackError("IMAP-Suche fehlgeschlagen.")
            ids = data[0].split() if data and data[0] else []
            if limit is not None:
                ids = ids[:limit]
            replies: list[Reply] = []
            for msg_id in ids:
                typ, msg_data = client.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                replies.append(self._parse(msg_data[0][1]))
            return replies
        except TrackError:
            raise
        except Exception as exc:
            raise TrackError(f"IMAP-Abruf fehlgeschlagen ({type(exc).__name__}).") from exc
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def classify(self, reply: Reply) -> ReplyKind:
        return self.classifier.classify(reply)

    def _search_criteria(self, since: datetime | None) -> str:
        if since is None:
            return "ALL"
        return f'(SINCE "{since.strftime("%d-%b-%Y")}")'

    def _parse(self, raw: bytes) -> Reply:
        msg = email.message_from_bytes(raw, policy=policy.default)
        message_id = _normalize_message_id(str(msg.get("Message-ID", "") or "")) or new_id("msgid")
        sender = parseaddr(str(msg.get("From", "")))[1] or str(msg.get("From", "") or "")
        subject = str(msg.get("Subject", "") or "")
        in_reply_to_raw = msg.get("In-Reply-To")
        in_reply_to = _normalize_message_id(str(in_reply_to_raw)) if in_reply_to_raw else None
        references = str(msg.get("References", "") or "")
        body = self._body_text(msg)
        draft_id = self._correlate(in_reply_to, references, subject)

        reply = Reply(
            message_id=message_id,
            sender=sender,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            draft_id=draft_id,
        )

        auto_submitted = str(msg.get("Auto-Submitted", "") or "").strip().lower()
        if auto_submitted and auto_submitted != "no":
            return replace(reply, kind=ReplyKind.AUTO_REPLY)
        return replace(reply, kind=self.classifier.classify(reply))

    def _body_text(self, msg: email.message.Message) -> str:
        body_part = msg.get_body(preferencelist=("plain", "html"))
        if body_part is None:
            return ""
        content = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            content = _TAG_RE.sub(" ", content)
        return content.strip()

    def _correlate(self, in_reply_to: str | None, references: str, subject: str) -> str | None:
        candidates = [in_reply_to] if in_reply_to else []
        candidates.extend(references.split())
        for token in candidates:
            draft_id = self._known_ids.get(_normalize_message_id(token))
            if draft_id:
                return draft_id
        return self._known_subjects.get(_strip_reply_prefix(subject))
