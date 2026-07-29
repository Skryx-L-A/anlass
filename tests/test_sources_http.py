"""The shared HTTP fetch: timeout, one retry, a speaking error. No socket opens."""

from __future__ import annotations

import urllib.error

import pytest

from anlass.errors import SourceError
from anlass.sources import _http


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self._body


def test_fetch_text_decodes_the_body(monkeypatch):
    monkeypatch.setattr(_http.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"hallo"))
    assert _http.fetch_text("https://beispiel.example") == "hallo"


def test_fetch_retries_once_after_a_server_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("kaputt")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(_http.urllib.request, "urlopen", flaky)
    assert _http.fetch_text("https://beispiel.example") == "ok"
    assert calls["n"] == 2


def test_fetch_gives_up_after_the_retry_with_a_speaking_error(monkeypatch):
    def always_fails(*args, **kwargs):
        raise urllib.error.URLError("dauerhaft kaputt")

    monkeypatch.setattr(_http.urllib.request, "urlopen", always_fails)
    with pytest.raises(SourceError) as excinfo:
        _http.fetch_text("https://beispiel.example")
    assert "beispiel.example" in str(excinfo.value)


def test_a_client_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def not_found(*args, **kwargs):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://beispiel.example", 404, "Not Found", None, None)

    monkeypatch.setattr(_http.urllib.request, "urlopen", not_found)
    with pytest.raises(SourceError):
        _http.fetch_text("https://beispiel.example")
    assert calls["n"] == 1


def test_never_waits_unboundedly_a_timeout_is_always_passed(monkeypatch):
    seen = {}

    def capture(request, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(b"x")

    monkeypatch.setattr(_http.urllib.request, "urlopen", capture)
    _http.fetch_text("https://beispiel.example", timeout=3.5)
    assert seen["timeout"] == 3.5
