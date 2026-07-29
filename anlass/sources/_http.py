"""Dependency-free HTTP fetch for sources and enrichment providers.

Mirrors :mod:`anlass.llm._http` in spirit - stdlib only, so this package stays
installable without a network client dependency - but lives here on purpose: stage
1 and stage 3 fetch web pages and feeds, which is a different job from talking to a
model provider, and neither should have to import the other's package to do it.

Every network access in this package goes through here, and every call carries a
timeout, one retry, and a speaking error: nothing in this codebase waits
unboundedly on the network.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Mapping

from ..errors import SourceError

__all__ = ["fetch_bytes", "fetch_text"]

DEFAULT_TIMEOUT = 20.0
DEFAULT_USER_AGENT = "anlass/0.1 (+https://github.com/Skryx-L-A/anlass)"


def fetch_bytes(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
) -> bytes:
    """GET ``url`` and return the raw body.

    A 4xx answer is not retried - the request itself is wrong, trying again would
    not help. A connection failure or a 5xx is retried once, then raises.

    Raises:
        anlass.errors.SourceError: The URL stayed unreachable or answered with an
            error after the retry.
    """
    merged = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    last_error: BaseException | None = None
    for attempt in range(max(1, retries + 1)):
        request = urllib.request.Request(url, headers=merged, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500:
                break
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
    raise SourceError(f"'{url}' ist nicht erreichbar: {last_error}") from last_error


def fetch_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
    encoding: str = "utf-8",
) -> str:
    """GET ``url`` and decode the body as text, replacing what does not decode."""
    return fetch_bytes(url, headers=headers, timeout=timeout, retries=retries).decode(encoding, errors="replace")
