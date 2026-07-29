"""Stage 1: fetch an organisation's career page and pull out postings.

Career pages share no common structure, so this does not attempt a bespoke parser
per framework. It takes the page's outbound links, keeps the ones that look like a
posting - a configurable pattern, defaulting to a small list of common German and
English job-page words - and fetches each one for its own text the same way
:class:`anlass.sources.url.UrlSource` would. That is the honest tradeoff: one HTTP
round trip per posting instead of a parser that would break on the next redesign,
because unlike stage 1's open job API a career page is not a documented interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
from urllib.parse import urljoin

from ..errors import SourceError
from ..models import Field, FieldValue, Lead, RawRecord
from . import _html, _http

__all__ = ["CareerPageSource"]

DEFAULT_KEYWORDS = (
    "job", "jobs", "stelle", "stellen", "stellenangebot", "karriere", "career",
    "careers", "position", "positions", "vacancy", "vacancies", "offene-stellen",
    "join-us", "praktikum", "werkstudent", "ausbildung", "bewerbung", "bewerben",
)


@dataclass
class CareerPageSource:
    """Args:
    url: The career page's own URL.
    link_pattern: Regular expression (case-insensitive) that a link's absolute URL
        or visible text must match to count as a posting. ``None`` falls back to
        :data:`DEFAULT_KEYWORDS`.
    fetch_postings: When true (default), each matched link is fetched for its full
        text; when false, only the link text is used - faster, coarser.
    """

    url: str
    link_pattern: str | None = None
    timeout: float = 20.0
    fetch_postings: bool = True

    @property
    def name(self) -> str:
        return "careerpage"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        html = _http.fetch_text(self.url, timeout=self.timeout)
        pattern = re.compile(self.link_pattern, re.IGNORECASE) if self.link_pattern else None
        seen: set[str] = set()
        count = 0
        for href, link_text in _html.extract_links(html):
            absolute = urljoin(self.url, href)
            if not absolute.startswith(("http://", "https://")) or absolute in seen:
                continue
            haystack = f"{absolute} {link_text}".lower()
            is_match = pattern.search(haystack) if pattern else any(word in haystack for word in DEFAULT_KEYWORDS)
            if not is_match:
                continue
            seen.add(absolute)
            text, title = link_text, link_text or None
            if self.fetch_postings:
                try:
                    posting_html = _http.fetch_text(absolute, timeout=self.timeout)
                except SourceError:
                    posting_html = None
                if posting_html is not None:
                    text = _html.html_to_text(posting_html) or link_text
                    title = _html.page_title(posting_html) or title
            if not text:
                continue
            yield RawRecord(
                source=self.name,
                external_id=absolute,
                text=text,
                url=absolute,
                data={"title": title, "link_text": link_text},
            )
            count += 1
            if limit is not None and count >= limit:
                return

    def normalize(self, record: RawRecord) -> Lead:
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        if record.url:
            lead.set(Field.URL, FieldValue(record.url, provider=self.name, confidence=1.0))
        title = record.data.get("title")
        if title:
            lead.set(Field.ROLE, FieldValue(title, provider=self.name, confidence=0.6, evidence=str(title)))
        return lead
