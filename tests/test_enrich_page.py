"""PageTextProvider: fetches the lead's own URL, fills `page_text`, never guesses."""

from __future__ import annotations

import pytest

from anlass.enrich.providers.page import MIN_OWN_TEXT_CHARS, PAGE_TEXT_FIELD, PageTextProvider
from anlass.errors import SourceError
from anlass.interfaces import EnrichProvider
from anlass.models import Field, FieldValue, Lead
from anlass.sources import _http

_HTML = "<html><body><p>Wir bauen unsere Datenverarbeitung selbst.</p></body></html>"


def test_conforms_to_the_protocol():
    assert isinstance(PageTextProvider(), EnrichProvider)
    assert PageTextProvider().provides == frozenset({PAGE_TEXT_FIELD})


def test_fetches_the_lead_url_and_extracts_text(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _HTML)
    lead = Lead(source="test")
    lead.set(Field.URL, FieldValue("https://beispiel.example/stelle", provider="quelle"))
    provider = PageTextProvider()
    found = provider.enrich(lead, [PAGE_TEXT_FIELD])
    assert "Wir bauen unsere Datenverarbeitung selbst." in found[PAGE_TEXT_FIELD].value
    assert found[PAGE_TEXT_FIELD].provider == "page"


def test_returns_nothing_when_the_lead_has_no_url():
    provider = PageTextProvider()
    assert provider.enrich(Lead(source="test"), [PAGE_TEXT_FIELD]) == {}


def test_returns_nothing_when_page_text_was_not_requested(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: (_ for _ in ()).throw(AssertionError("sollte nicht abrufen")))
    lead = Lead(source="test")
    lead.set(Field.URL, FieldValue("https://beispiel.example/stelle", provider="quelle"))
    assert PageTextProvider().enrich(lead, [Field.ORGANIZATION]) == {}


def test_an_unreachable_page_propagates_the_source_error_it_does_not_hide_an_outage(monkeypatch):
    def boom(url, **kwargs):
        raise SourceError("nicht erreichbar")

    monkeypatch.setattr(_http, "fetch_text", boom)
    lead = Lead(source="test")
    lead.set(Field.URL, FieldValue("https://beispiel.example/stelle", provider="quelle"))
    with pytest.raises(SourceError):
        PageTextProvider().enrich(lead, [PAGE_TEXT_FIELD])


def test_a_lead_that_already_carries_the_posting_is_not_fetched(monkeypatch):
    """Q11: fetching adds nothing when the source already gave the
    full text, only 15+ thousand characters of a job board's own chrome around it."""
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: (_ for _ in ()).throw(AssertionError("sollte nicht abrufen")))
    lead = Lead(source="test", text="A" * MIN_OWN_TEXT_CHARS)
    lead.set(Field.URL, FieldValue("https://beispiel.example/stelle", provider="quelle"))
    assert PageTextProvider().enrich(lead, [PAGE_TEXT_FIELD]) == {}


def test_a_short_teaser_still_gets_the_page_fetched(monkeypatch):
    """The RSS case this provider exists for: a headline is not a usable text."""
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _HTML)
    lead = Lead(source="test", text="Kurzer Teaser.")
    lead.set(Field.URL, FieldValue("https://beispiel.example/stelle", provider="quelle"))
    found = PageTextProvider().enrich(lead, [PAGE_TEXT_FIELD])
    assert "Wir bauen unsere Datenverarbeitung selbst." in found[PAGE_TEXT_FIELD].value
