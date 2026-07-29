"""Stage 1: RSS 2.0 and Atom feeds.

Parsed with the standard library's :mod:`xml.etree.ElementTree`. Both formats are
shallow, well-documented XML, so a feed-parsing dependency is not worth carrying
for two known tag sets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterator
from xml.etree import ElementTree

from ..errors import SourceError
from ..models import Field, FieldValue, Lead, RawRecord
from . import _html, _http

__all__ = ["RssSource"]

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass
class RssSource:
    url: str
    timeout: float = 20.0

    @property
    def name(self) -> str:
        return "rss"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        raw = _http.fetch_text(self.url, timeout=self.timeout)
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise SourceError(f"'{self.url}' ist kein gueltiges RSS/Atom-XML: {exc}") from exc
        count = 0
        for entry_id, title, link, summary, published in self._entries(root):
            if since is not None and published is not None and published <= since:
                continue
            text = (_html.html_to_text(summary) if summary else "") or (title or "")
            if not text:
                continue
            yield RawRecord(
                source=self.name,
                external_id=entry_id or link or title or "",
                text=text,
                url=link,
                data={"title": title, "published_at": published.isoformat() if published else None},
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
        published = record.data.get("published_at")
        if published:
            lead.set(Field.PUBLISHED_AT, FieldValue(published, provider=self.name, confidence=1.0))
        return lead

    def _entries(self, root: ElementTree.Element):
        channel = root.find("channel")
        if root.tag == "rss" or channel is not None:
            for item in (channel.findall("item") if channel is not None else []):
                yield (
                    _text(item, "guid"),
                    _text(item, "title"),
                    _text(item, "link"),
                    _text(item, "description"),
                    _parse_rfc822(_text(item, "pubDate")),
                )
            return
        if root.tag in (f"{_ATOM_NS}feed", "feed"):
            entries = root.findall(f"{_ATOM_NS}entry") or root.findall("entry")
            for entry in entries:
                link_el = _find(entry, f"{_ATOM_NS}link", "link")
                summary = (
                    _text(entry, f"{_ATOM_NS}summary")
                    or _text(entry, "summary")
                    or _text(entry, f"{_ATOM_NS}content")
                    or _text(entry, "content")
                )
                pub_raw = (
                    _text(entry, f"{_ATOM_NS}updated")
                    or _text(entry, "updated")
                    or _text(entry, f"{_ATOM_NS}published")
                    or _text(entry, "published")
                )
                yield (
                    _text(entry, f"{_ATOM_NS}id") or _text(entry, "id"),
                    _text(entry, f"{_ATOM_NS}title") or _text(entry, "title"),
                    link_el.get("href") if link_el is not None else None,
                    summary,
                    _parse_iso(pub_raw),
                )
            return
        raise SourceError(f"'{self.url}' ist weder RSS noch Atom (Wurzelelement '{root.tag}').")


def _find(el: ElementTree.Element, *tags: str) -> ElementTree.Element | None:
    for tag in tags:
        found = el.find(tag)
        if found is not None:
            return found
    return None


def _text(el: ElementTree.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
