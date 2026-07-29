"""Enrichment provider: fetch the lead's own URL and store its visible text.

Downstream extraction works from prose, but a source like ``rss`` only ever hands
over a short summary. This provider is the bridge: it fetches the page behind
``Field.URL`` once and stores the extracted text under ``PAGE_TEXT_FIELD``, so
:mod:`anlass.enrich.providers.extract` has real material instead of a two-sentence
teaser.

It does that only when the lead needs it (Q11). Measured on the
two postings: both arrive from a file source with the full text already in
``lead.text`` (1.996 and 1.396 characters), and the fetched page added nothing but
15.586 and 18.156 characters of boilerplate - even after :mod:`._html` strips
navigation, footers and consent banners, a fetched job-board page still carries
other postings, related-jobs widgets and site chrome that no amount of tag
stripping removes, because it is not markup, it is content. Below
:data:`MIN_OWN_TEXT_CHARS` the source only handed over a headline or a two-sentence
teaser, and the page is the only material there is - fetching stays the point of
this provider for exactly that case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ...models import Field, FieldValue, Lead
from ...sources import _html, _http

__all__ = ["PageTextProvider", "PAGE_TEXT_FIELD", "MIN_OWN_TEXT_CHARS"]

PAGE_TEXT_FIELD = "page_text"

#: From this length on, the lead's own text is treated as the posting itself, and
#: fetching the page is skipped outright. Below it, the source handed over a
#: headline or a two-sentence teaser and the page is the only material there is.
#: Shared with :mod:`anlass.enrich.providers.extract`, which makes the same
#: judgement call about which text to read once both exist.
MIN_OWN_TEXT_CHARS = 200


@dataclass
class PageTextProvider:
    timeout: float = 20.0

    @property
    def name(self) -> str:
        return "page"

    @property
    def provides(self) -> frozenset[str]:
        return frozenset({PAGE_TEXT_FIELD})

    def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
        if PAGE_TEXT_FIELD not in missing:
            return {}
        if len(str(lead.text or "").strip()) >= MIN_OWN_TEXT_CHARS:
            return {}
        url = lead.value(Field.URL)
        if not url:
            return {}
        html_source = _http.fetch_text(str(url), timeout=self.timeout)
        text = _html.html_to_text(html_source)
        if not text:
            return {}
        return {PAGE_TEXT_FIELD: FieldValue(text, provider=self.name, confidence=1.0, evidence=text[:200])}
