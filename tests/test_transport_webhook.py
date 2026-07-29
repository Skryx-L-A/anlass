from __future__ import annotations

import pytest

from anlass.models import OutboundMessage
from anlass.transport import TransportError
from anlass.transport.webhook import WebhookTransport


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


def test_send_posts_the_message_as_json() -> None:
    calls = []

    def fake_poster(url, payload, *, headers, timeout):
        calls.append((url, payload, headers, timeout))
        return {"ok": True, "id": "hook-1"}

    transport = WebhookTransport(url="https://n8n.beispiel.example/webhook/x", poster=fake_poster)
    receipt = transport.send(_message())

    assert receipt.transport == "webhook"
    assert receipt.accepted is True
    assert receipt.reference == "hook-1"
    assert len(calls) == 1
    url, payload, headers, timeout = calls[0]
    assert url == "https://n8n.beispiel.example/webhook/x"
    assert payload["draft_id"] == "draft_test"
    assert payload["recipient"] == "roth@nordlicht.example"


def test_send_reports_rejection_from_the_endpoint() -> None:
    transport = WebhookTransport(
        url="https://n8n.beispiel.example/webhook/x",
        poster=lambda url, payload, *, headers, timeout: {"ok": False, "id": "hook-2"},
    )
    receipt = transport.send(_message())

    assert receipt.accepted is False
    assert receipt.reference == "hook-2"


def test_send_raises_on_transport_failure_without_opening_a_real_connection() -> None:
    def failing_poster(url, payload, *, headers, timeout):
        raise OSError("nicht erreichbar")

    transport = WebhookTransport(url="https://n8n.beispiel.example/webhook/x", poster=failing_poster)

    with pytest.raises(TransportError):
        transport.send(_message())


def test_preflight_flags_a_non_https_url() -> None:
    transport = WebhookTransport(url="http://n8n.beispiel.example/webhook/x")
    problems = transport.preflight(_message())

    assert problems
    assert "https" in problems[0]


def test_preflight_is_clean_for_an_https_url() -> None:
    transport = WebhookTransport(url="https://n8n.beispiel.example/webhook/x")
    assert transport.preflight(_message()) == []
