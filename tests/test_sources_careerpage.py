"""CareerPageSource: links filtered by keyword/pattern, each posting fetched."""

from __future__ import annotations

from anlass.errors import SourceError
from anlass.models import Field
from anlass.sources import _http
from anlass.sources.careerpage import CareerPageSource

_CAREER_PAGE = """
<html><body>
<nav>
<a href="/impressum">Impressum</a>
<a href="/karriere/werkstudent-datenverarbeitung">Werkstudent Datenverarbeitung</a>
<a href="https://extern.example/praktikum-backend">Praktikum Backend</a>
</nav>
</body></html>
"""

_POSTING_1 = "<html><head><title>Werkstudent Datenverarbeitung</title></head><body><p>Wir bauen selbst.</p></body></html>"
_POSTING_2 = "<html><head><title>Praktikum Backend</title></head><body><p>Python-Team.</p></body></html>"

_PAGES = {
    "https://beispiel.example/karriere": _CAREER_PAGE,
    "https://beispiel.example/karriere/werkstudent-datenverarbeitung": _POSTING_1,
    "https://extern.example/praktikum-backend": _POSTING_2,
}


def _fake_fetch(url, **kwargs):
    if url not in _PAGES:
        raise SourceError(f"unbekannt in diesem Test: {url}")
    return _PAGES[url]


def test_keeps_only_links_that_look_like_postings(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", _fake_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere")
    records = list(source.fetch())
    urls = {r.url for r in records}
    assert urls == {
        "https://beispiel.example/karriere/werkstudent-datenverarbeitung",
        "https://extern.example/praktikum-backend",
    }


def test_fetches_each_postings_own_text(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", _fake_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere")
    records = {r.url: r for r in source.fetch()}
    posting = records["https://beispiel.example/karriere/werkstudent-datenverarbeitung"]
    assert "Wir bauen selbst." in posting.text
    assert posting.data["title"] == "Werkstudent Datenverarbeitung"


def test_fetch_postings_false_uses_only_link_text(monkeypatch):
    calls = []

    def counting_fetch(url, **kwargs):
        calls.append(url)
        return _PAGES[url]

    monkeypatch.setattr(_http, "fetch_text", counting_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere", fetch_postings=False)
    records = list(source.fetch())
    assert calls == ["https://beispiel.example/karriere"]
    assert all(r.text for r in records)


def test_custom_link_pattern_overrides_the_keyword_list(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", _fake_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere", link_pattern=r"impressum")
    records = list(source.fetch())
    assert len(records) == 1
    assert records[0].url == "https://beispiel.example/impressum" or "impressum" in records[0].url


def test_normalize_sets_url_and_role(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", _fake_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere")
    lead = source.normalize(next(source.fetch()))
    assert lead.value(Field.URL)
    assert lead.value(Field.ROLE)


def test_limit_caps_postings(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", _fake_fetch)
    source = CareerPageSource(url="https://beispiel.example/karriere")
    assert len(list(source.fetch(limit=1))) == 1
