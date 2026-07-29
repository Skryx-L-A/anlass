"""OpenApiSource: paginated JSON, `since`/`limit`, configurable base URL, no key."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from anlass.errors import SourceError
from anlass.models import Field
from anlass.sources import _http
from anlass.sources.openapi import DEFAULT_BASE_URL, OpenApiSource

_PAGE_1 = {
    "data": [
        {
            "slug": "werkstudent-1",
            "company_name": "Nordlicht Systeme",
            "title": "Werkstudent Datenverarbeitung",
            "description": "&lt;p&gt;Wir bauen unsere Datenverarbeitung selbst.&lt;/p&gt;",
            "remote": True,
            "url": "https://beispiel.example/jobs/werkstudent-1",
            "tags": ["Python", "SQL"],
            "job_types": ["Werkstudent"],
            "location": "Berlin",
            "created_at": 1785200000,
        }
    ],
    "links": {"next": "https://api.example/jobs?page=2"},
}

_PAGE_2 = {
    "data": [
        {
            "slug": "praktikum-2",
            "company_name": "Beispiel GmbH",
            "title": "Praktikum Backend",
            "description": "Python-Team sucht Verstaerkung.",
            "remote": False,
            "url": "https://beispiel.example/jobs/praktikum-2",
            "tags": [],
            "job_types": [],
            "location": "Muenchen",
            "created_at": 1785300000,
        }
    ],
    "links": {"next": None},
}

_PAGES = {
    "https://api.example/jobs": _PAGE_1,
    "https://api.example/jobs?page=2": _PAGE_2,
}


def test_default_base_url_is_arbeitnow_but_configurable():
    assert "arbeitnow.com" in DEFAULT_BASE_URL
    assert OpenApiSource(base_url="https://api.example/jobs").base_url == "https://api.example/jobs"


def test_follows_pagination_via_links_next(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps(_PAGES[url]))
    source = OpenApiSource(base_url="https://api.example/jobs")
    records = list(source.fetch())
    assert [r.external_id for r in records] == ["werkstudent-1", "praktikum-2"]


def test_description_is_unescaped_and_stripped_of_tags(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps(_PAGES[url]))
    source = OpenApiSource(base_url="https://api.example/jobs")
    record = next(source.fetch())
    assert "Wir bauen unsere Datenverarbeitung selbst." in record.text
    assert "&lt;" not in record.text and "<p>" not in record.text


def test_since_filters_by_created_at(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps(_PAGES[url]))
    cutoff = datetime.fromtimestamp(1785250000, tz=timezone.utc)
    source = OpenApiSource(base_url="https://api.example/jobs")
    records = list(source.fetch(since=cutoff))
    assert [r.external_id for r in records] == ["praktikum-2"]


def test_limit_stops_across_pages(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps(_PAGES[url]))
    source = OpenApiSource(base_url="https://api.example/jobs")
    assert len(list(source.fetch(limit=1))) == 1


def test_normalize_maps_the_known_fields(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps(_PAGES[url]))
    source = OpenApiSource(base_url="https://api.example/jobs")
    lead = source.normalize(next(source.fetch()))
    assert lead.value(Field.ORGANIZATION) == "Nordlicht Systeme"
    assert lead.value(Field.ROLE) == "Werkstudent Datenverarbeitung"
    assert lead.value(Field.LOCATION) == "Berlin"
    assert lead.value(Field.REMOTE) is True
    assert lead.value(Field.TECH_STACK) == ["Python", "SQL"]
    assert lead.value(Field.EMPLOYMENT_TYPE) == "Werkstudent"
    assert lead.value(Field.PUBLISHED_AT) is not None


def test_malformed_json_is_a_source_error(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: "{not json")
    with pytest.raises(SourceError):
        list(OpenApiSource(base_url="https://api.example/jobs").fetch())


def test_missing_data_field_is_a_source_error(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: json.dumps({"nope": []}))
    with pytest.raises(SourceError):
        list(OpenApiSource(base_url="https://api.example/jobs").fetch())
