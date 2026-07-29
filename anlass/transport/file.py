"""Write the finished message into a folder. The default transport.

Nothing here reaches past the local filesystem, so it is what every example, dry run
and first-time setup uses before real credentials exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import OutboundMessage, TransportReceipt, new_id
from . import TransportError

__all__ = ["FileTransport"]


@dataclass
class FileTransport:
    """Drops every message as one text file into ``directory``.

    Args:
        directory: Created on first use if it does not exist yet.
    """

    directory: str | Path = "outbox"

    @property
    def name(self) -> str:
        return "file"

    def preflight(self, message: OutboundMessage) -> list[str]:
        """Nothing to check: a local file has no deliverability of its own."""
        return []

    def send(self, message: OutboundMessage) -> TransportReceipt:
        out_dir = Path(self.directory)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransportError(f"Ordner '{out_dir}' konnte nicht angelegt werden: {exc}") from exc

        path = out_dir / f"{message.draft_id}_{new_id('msg')}.eml"
        lines = [
            f"To: {message.recipient}",
            f"Subject: {message.subject}",
        ]
        if message.sender:
            lines.append(f"From: {message.sender}")
        for key, value in message.headers.items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append(message.body)
        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            raise TransportError(f"Nachricht konnte nicht nach '{path}' geschrieben werden: {exc}") from exc

        return TransportReceipt(
            transport=self.name,
            accepted=True,
            reference=str(path),
            detail="In Ordner abgelegt, nicht verschickt.",
        )
