"""Enrichment provider: resolve the organisation's own website from the posting page.

Purely mechanical - no model, no search API (none is free without a key). The
posting page is fetched once, its outbound links are read, and the first one that
does not point back at a known job board, social network, or the posting's own
host is taken as the organisation's site. That is a heuristic and it says so:
confidence stays below 1.0, and nothing is invented that is not literally an
``href`` on the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence
from urllib.parse import urljoin, urlparse

from ...models import Field, FieldValue, Lead
from ...sources import _html, _http

__all__ = ["OrgWebsiteProvider", "ORG_WEBSITE_FIELD", "DEFAULT_EXCLUDED_HOSTS"]

ORG_WEBSITE_FIELD = "org_website"

#: Hosts that are never the organisation's own site - job boards, social networks,
#: and other usual suspects that show up as outbound links on a posting page.
DEFAULT_EXCLUDED_HOSTS = frozenset(
    {
        "arbeitnow.com", "indeed.com", "indeed.de", "linkedin.com", "xing.com",
        "stepstone.de", "stepstone.com", "glassdoor.com", "glassdoor.de",
        "kununu.com", "monster.de", "monster.com", "facebook.com", "twitter.com",
        "x.com", "instagram.com", "youtube.com", "google.com", "goo.gl",
    }
)


@dataclass
class OrgWebsiteProvider:
    timeout: float = 20.0
    excluded_hosts: frozenset[str] = field(default_factory=lambda: DEFAULT_EXCLUDED_HOSTS)

    @property
    def name(self) -> str:
        return "orgsite"

    @property
    def provides(self) -> frozenset[str]:
        return frozenset({ORG_WEBSITE_FIELD})

    def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
        if ORG_WEBSITE_FIELD not in missing:
            return {}
        url = lead.value(Field.URL)
        if not url:
            return {}
        html_source = _http.fetch_text(str(url), timeout=self.timeout)
        posting_host = _host_of(str(url))
        for href, link_text in _html.extract_links(html_source):
            absolute = urljoin(str(url), href)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            host = _strip_www(parsed.netloc.lower())
            if host == posting_host or self._is_excluded(host):
                continue
            found = FieldValue(
                f"{parsed.scheme}://{parsed.netloc}",
                provider=self.name,
                confidence=0.5,
                evidence=link_text or href,
            )
            return {ORG_WEBSITE_FIELD: found}
        return {}

    def _is_excluded(self, host: str) -> bool:
        return any(host == excluded or host.endswith(f".{excluded}") for excluded in self.excluded_hosts)


def _host_of(url: str) -> str:
    return _strip_www(urlparse(url).netloc.lower())


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host
