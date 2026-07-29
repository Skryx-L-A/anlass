"""Minimal JSON over HTTP on the standard library.

Kept deliberately dependency-free: the package must stay installable without network
beyond its own install, and an HTTP client is not worth a dependency here. Tests
monkeypatch :func:`post_json` and :func:`get_json`; no test ever opens a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping

from ..errors import LLMError

__all__ = ["get_json", "post_json"]


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST ``payload`` as JSON and return the decoded answer."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return _send(request, timeout, url)


def get_json(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """GET a JSON document."""
    request = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return _send(request, timeout, url)


def _send(request: urllib.request.Request, timeout: float, url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - needs a live server
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"Der Modellanbieter antwortete mit Fehler {exc.code} ({url}): {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - needs a live server
        raise LLMError(f"Der Modellanbieter ist nicht erreichbar ({url}): {exc.reason}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Der Modellanbieter lieferte kein gueltiges JSON ({url}).") from exc
    if not isinstance(decoded, dict):
        raise LLMError(f"Der Modellanbieter lieferte kein JSON-Objekt ({url}).")
    return decoded
