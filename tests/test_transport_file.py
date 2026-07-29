from __future__ import annotations

from pathlib import Path

from anlass.models import OutboundMessage
from anlass.transport.file import FileTransport


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


def test_preflight_is_always_empty() -> None:
    transport = FileTransport(directory="unused")
    assert transport.preflight(_message()) == []


def test_send_writes_a_file_with_the_message(tmp_path: Path) -> None:
    transport = FileTransport(directory=tmp_path / "outbox")
    receipt = transport.send(_message())

    assert receipt.transport == "file"
    assert receipt.accepted is True
    written = Path(receipt.reference)
    assert written.is_file()
    content = written.read_text(encoding="utf-8")
    assert "Ihre Ausschreibung" in content
    assert "roth@nordlicht.example" in content
    assert "Sehr geehrte Frau Roth" in content


def test_send_includes_extra_headers(tmp_path: Path) -> None:
    transport = FileTransport(directory=tmp_path / "outbox")
    message = _message(headers={"List-Unsubscribe": "<mailto:abmelden@beispiel.example>"})
    receipt = transport.send(message)

    content = Path(receipt.reference).read_text(encoding="utf-8")
    assert "List-Unsubscribe" in content


def test_send_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "outbox"
    transport = FileTransport(directory=target)
    transport.send(_message())

    assert target.is_dir()


def test_two_sends_do_not_collide(tmp_path: Path) -> None:
    transport = FileTransport(directory=tmp_path / "outbox")
    first = transport.send(_message())
    second = transport.send(_message())

    assert first.reference != second.reference
