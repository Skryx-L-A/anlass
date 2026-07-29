"""OrgWebsiteProvider: a mechanical, evidence-only guess at the organisation's site."""

from __future__ import annotations

from anlass.enrich.providers.orgsite import ORG_WEBSITE_FIELD, OrgWebsiteProvider
from anlass.interfaces import EnrichProvider
from anlass.models import Field, FieldValue, Lead
from anlass.sources import _http

_POSTING_HTML = """
<html><body>
<a href="https://www.arbeitnow.com/other-jobs">Weitere Jobs</a>
<a href="https://www.linkedin.com/company/nordlicht">LinkedIn</a>
<a href="https://nordlicht-systeme.example/karriere">Zur Unternehmensseite</a>
</body></html>
"""

_NOTHING_HTML = """
<html><body>
<a href="https://www.xing.com/company/nordlicht">Xing</a>
<a href="https://beispiel.example/jobs/andere-stelle">Aehnliche Stellen</a>
</body></html>
"""


def _lead_with_url(url: str) -> Lead:
    lead = Lead(source="test")
    lead.set(Field.URL, FieldValue(url, provider="quelle"))
    return lead


def test_conforms_to_the_protocol():
    assert isinstance(OrgWebsiteProvider(), EnrichProvider)
    assert OrgWebsiteProvider().provides == frozenset({ORG_WEBSITE_FIELD})


def test_picks_the_first_link_that_is_not_a_job_board_or_social_network(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _POSTING_HTML)
    lead = _lead_with_url("https://beispiel.example/jobs/werkstudent")
    found = OrgWebsiteProvider().enrich(lead, [ORG_WEBSITE_FIELD])
    assert found[ORG_WEBSITE_FIELD].value == "https://nordlicht-systeme.example"
    assert found[ORG_WEBSITE_FIELD].confidence < 1.0
    assert found[ORG_WEBSITE_FIELD].evidence


def test_excludes_the_postings_own_host_too(monkeypatch):
    html_with_self_link = _POSTING_HTML.replace(
        "https://nordlicht-systeme.example/karriere",
        "https://beispiel.example/jobs/andere-stelle",
    )
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: html_with_self_link)
    lead = _lead_with_url("https://beispiel.example/jobs/werkstudent")
    assert OrgWebsiteProvider().enrich(lead, [ORG_WEBSITE_FIELD]) == {}


def test_returns_nothing_when_every_link_is_excluded(monkeypatch):
    monkeypatch.setattr(_http, "fetch_text", lambda url, **kwargs: _NOTHING_HTML)
    lead = _lead_with_url("https://beispiel.example/jobs/werkstudent")
    assert OrgWebsiteProvider().enrich(lead, [ORG_WEBSITE_FIELD]) == {}


def test_returns_nothing_when_the_lead_has_no_url():
    assert OrgWebsiteProvider().enrich(Lead(source="test"), [ORG_WEBSITE_FIELD]) == {}


def test_returns_nothing_when_not_requested(monkeypatch):
    monkeypatch.setattr(
        _http, "fetch_text", lambda url, **kwargs: (_ for _ in ()).throw(AssertionError("sollte nicht abrufen"))
    )
    lead = _lead_with_url("https://beispiel.example/jobs/werkstudent")
    assert OrgWebsiteProvider().enrich(lead, [Field.ORGANIZATION]) == {}
