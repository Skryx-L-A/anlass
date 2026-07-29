"""HTTP POST, so n8n or a CRM can dock on.

The payload is the whole ``OutboundMessage`` as JSON; what the receiving side does with
it is outside this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..models import OutboundMessage, TransportReceipt
from . import TransportError
from ._http import post_json

__all__ = ["WebhookTransport"]


@dataclass
class WebhookTransport:
    """Posts the message to ``url`` as JSON.

    Args:
        url: Target endpoint.
        headers: Extra headers, e.g. an auth token. Read at construction time by the
            caller, same as any other credential - this class only forwards them.
        timeout: Seconds to wait for the endpoint to answer.
        poster: Injectable HTTP function for tests; defaults to a real POST.
    """

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 15.0
    poster: Callable[..., Mapping[str, Any]] = field(default=post_json)

    @property
    def name(self) -> str:
        return "webhook"

    def preflight(self, message: OutboundMessage) -> list[str]:
        """A webhook has no mailbox reputation; only the endpoint itself is checked."""
        if not self.url.lower().startswith("https://"):
            return [f"Webhook-URL '{self.url}' ist nicht https, die Nachricht ginge unverschluesselt raus."]
        return []

    def send(self, message: OutboundMessage) -> TransportReceipt:
        payload = {
            "draft_id": message.draft_id,
            "recipient": message.recipient,
            "subject": message.subject,
            "body": message.body,
            "sender": message.sender,
            "headers": dict(message.headers),
        }
        try:
            response = self.poster(self.url, payload, headers=self.headers, timeout=self.timeout)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"Webhook-Versand fehlgeschlagen ({type(exc).__name__}).") from exc

        accepted = bool(response.get("ok", True)) if isinstance(response, Mapping) else True
        reference = str(response.get("id", "")) if isinstance(response, Mapping) else ""
        return TransportReceipt(transport=self.name, accepted=accepted, reference=reference, detail="")
