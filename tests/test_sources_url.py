"""UrlSource: one posting per fetch, text extracted from the fetched HTML."""

from __future__ import annotations

import pytest

from anlass.errors import SourceError
from anlass.models import Field
from anlass.sources import _http
from anlass.sources.url import UrlSource

_HTML = """
<html><head><title>Werkstudent Datenverarbeitung</title></head>
<body>
<script>ignoriere(mich);</script>
<h1>Werkstudent Datenverarbeitung</h1>
<p>Wir bauen unsere Datenverarbeitung selbst und suchen Verstaerkung.</p>
</body></html>
"""


def test_fetch_extracts_text_and_title(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _HTML)
    source = UrlSource(url="https://beispiel.example/stelle")
    records = list(source.fetch())
    assert len(records) == 1
    record = records[0]
    assert "Wir bauen unsere Datenverarbeitung selbst" in record.text
    assert "ignoriere(mich)" not in record.text
    assert record.data["title"] == "Werkstudent Datenverarbeitung"
    assert record.url == "https://beispiel.example/stelle"


def test_normalize_sets_url_and_role_with_reduced_confidence(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _HTML)
    source = UrlSource(url="https://beispiel.example/stelle")
    lead = source.normalize(next(source.fetch()))
    assert lead.value(Field.URL) == "https://beispiel.example/stelle"
    assert lead.value(Field.ROLE) == "Werkstudent Datenverarbeitung"
    assert lead.fields[Field.ROLE].confidence < 1.0


def test_limit_zero_yields_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: calls.append(url) or _HTML)
    assert list(UrlSource(url="https://beispiel.example").fetch(limit=0)) == []
    assert calls == []


def test_a_page_without_text_is_a_source_error(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: "<html><body></body></html>")
    with pytest.raises(SourceError):
        list(UrlSource(url="https://beispiel.example").fetch())


def test_unreachable_url_propagates_the_source_error(monkeypatch):
    def boom(url, **kwargs):
        raise SourceError("nicht erreichbar")

    monkeypatch.setattr(_http, "fetch_text", boom)
    with pytest.raises(SourceError):
        list(UrlSource(url="https://beispiel.example").fetch())
