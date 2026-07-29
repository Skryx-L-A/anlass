"""Stage 1: a single job posting given by URL, text extracted from the page.

One posting per fetch - there is no listing to page through, so ``since``/``limit``
only decide whether the single record is yielded at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from ..errors import SourceError
from ..models import Field, FieldValue, Lead, RawRecord
from . import _html, _http

__all__ = ["UrlSource"]


@dataclass
class UrlSource:
    url: str
    timeout: float = 20.0

    @property
    def name(self) -> str:
        return "url"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        if limit is not None and limit <= 0:
            return
        html = _http.fetch_text(self.url, timeout=self.timeout)
        text = _html.html_to_text(html)
        if not text:
            raise SourceError(f"'{self.url}' lieferte keinen erkennbaren Text.")
        title = _html.page_title(html)
        yield RawRecord(source=self.name, external_id=self.url, text=text, url=self.url, data={"title": title})

    def normalize(self, record: RawRecord) -> Lead:
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        if record.url:
            lead.set(Field.URL, FieldValue(record.url, provider=self.name, confidence=1.0))
        title = record.data.get("title")
        if title:
            lead.set(Field.ROLE, FieldValue(title, provider=self.name, confidence=0.5, evidence=str(title)))
        return lead
