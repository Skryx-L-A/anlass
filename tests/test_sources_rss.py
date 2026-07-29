"""RssSource: RSS 2.0 and Atom, `since` filtering, malformed XML raises."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from anlass.errors import SourceError
from anlass.models import Field
from anlass.sources import _http
from anlass.sources.rss import RssSource

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Stellenanzeigen</title>
<item>
  <title>Werkstudent Datenverarbeitung</title>
  <link>https://beispiel.example/1</link>
  <guid>guid-1</guid>
  <description>&lt;p&gt;Wir bauen unsere Datenverarbeitung selbst.&lt;/p&gt;</description>
  <pubDate>Mon, 20 Jul 2026 10:00:00 +0000</pubDate>
</item>
<item>
  <title>Praktikum Backend</title>
  <link>https://beispiel.example/2</link>
  <guid>guid-2</guid>
  <description>Backend in Python.</description>
  <pubDate>Wed, 22 Jul 2026 10:00:00 +0000</pubDate>
</item>
</channel></rss>
"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <id>atom-1</id>
  <title>Werkstudent Datenverarbeitung</title>
  <link href="https://beispiel.example/atom-1"/>
  <summary>Wir bauen unsere Datenverarbeitung selbst.</summary>
  <updated>2026-07-20T10:00:00Z</updated>
</entry>
</feed>
"""


def test_parses_rss_items(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _RSS)
    records = list(RssSource(url="https://beispiel.example/feed.xml").fetch())
    assert len(records) == 2
    assert records[0].external_id == "guid-1"
    assert "Wir bauen unsere Datenverarbeitung selbst." in records[0].text
    assert records[0].url == "https://beispiel.example/1"


def test_rss_since_filters_older_items(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _RSS)
    cutoff = datetime(2026, 7, 21, tzinfo=timezone.utc)
    records = list(RssSource(url="https://beispiel.example/feed.xml").fetch(since=cutoff))
    assert len(records) == 1
    assert records[0].data["title"] == "Praktikum Backend"


def test_rss_limit_caps_items(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _RSS)
    assert len(list(RssSource(url="https://beispiel.example/feed.xml").fetch(limit=1))) == 1


def test_parses_atom_entries(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _ATOM)
    records = list(RssSource(url="https://beispiel.example/feed.atom").fetch())
    assert len(records) == 1
    assert records[0].external_id == "atom-1"
    assert records[0].url == "https://beispiel.example/atom-1"


def test_normalize_sets_role_url_and_published_at(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _RSS)
    source = RssSource(url="https://beispiel.example/feed.xml")
    lead = source.normalize(next(source.fetch()))
    assert lead.value(Field.ROLE) == "Werkstudent Datenverarbeitung"
    assert lead.value(Field.URL) == "https://beispiel.example/1"
    assert lead.value(Field.PUBLISHED_AT) is not None


def test_malformed_xml_is_a_source_error(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: "<not-xml")
    with pytest.raises(SourceError):
        list(RssSource(url="https://beispiel.example/feed.xml").fetch())


def test_unrecognised_root_element_is_a_source_error(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: "<somethingelse/>")
    with pytest.raises(SourceError):
        list(RssSource(url="https://beispiel.example/feed.xml").fetch())
