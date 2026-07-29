"""Minimal JSON-over-HTTP POST, standard library only.

A private copy of the same small idea as :mod:`anlass.llm._http`: kept dependency-free,
and kept local to this package rather than imported across the phase-2 worker boundary
so this module does not depend on another owner's internals. Tests inject a fake poster
into :class:`~anlass.transport.webhook.WebhookTransport`; nothing here is exercised by
the test suite directly.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping

from . import TransportError

__all__ = ["post_json"]


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST ``payload`` as JSON and return the decoded answer, or ``{}`` for no body."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - needs a live server
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise TransportError(f"Webhook antwortete mit Fehler {exc.code} ({url}): {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - needs a live server
        raise TransportError(f"Webhook nicht erreichbar ({url}): {exc.reason}") from exc
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
