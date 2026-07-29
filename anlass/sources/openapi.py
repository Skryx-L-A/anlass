"""Stage 1: an open job API with a documented, key-free schema.

Default is `arbeitnow.com <https://www.arbeitnow.com/api/job-board-api>`_'s job
board API - free, no key, paginated JSON. ``base_url`` is a constructor argument,
never hard-coded past that default, so this file works against any API that
returns the same ``{"data": [...], "links": {"next": ...}}`` shape, not only
arbeitnow's.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from ..errors import SourceError
from ..models import Field, FieldValue, Lead, RawRecord
from . import _html as _htmltools
from . import _http

__all__ = ["OpenApiSource"]

DEFAULT_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"


@dataclass
class OpenApiSource:
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    max_pages: int = 5

    @property
    def name(self) -> str:
        return "openapi"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        count = 0
        url: str | None = self.base_url
        pages = 0
        while url and pages < self.max_pages:
            pages += 1
            raw = _http.fetch_text(url, timeout=self.timeout)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SourceError(f"'{url}' lieferte kein gueltiges JSON: {exc}") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise SourceError(f"'{url}' hat nicht die erwartete Form (Feld 'data' fehlt).")
            for entry in payload["data"]:
                if not isinstance(entry, dict):
                    continue
                published = _parse_timestamp(entry.get("created_at"))
                if since is not None and published is not None and published <= since:
                    continue
                external_id = str(entry.get("slug") or entry.get("url") or entry.get("id") or "")
                text = _job_text(entry)
                if not external_id or not text:
                    continue
                record_url = entry.get("url")
                yield RawRecord(
                    source=self.name,
                    external_id=external_id,
                    text=text,
                    url=str(record_url) if record_url else None,
                    data=entry,
                )
                count += 1
                if limit is not None and count >= limit:
                    return
            links = payload.get("links")
            url = links.get("next") if isinstance(links, dict) else None

    def normalize(self, record: RawRecord) -> Lead:
        data = record.data
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        if record.url:
            lead.set(Field.URL, FieldValue(record.url, provider=self.name, confidence=1.0))
        _set(lead, self.name, Field.ORGANIZATION, data.get("company_name"))
        _set(lead, self.name, Field.ROLE, data.get("title"))
        _set(lead, self.name, Field.LOCATION, data.get("location"))
        remote = data.get("remote")
        if isinstance(remote, bool):
            lead.set(Field.REMOTE, FieldValue(remote, provider=self.name, confidence=1.0))
        tags = data.get("tags")
        if isinstance(tags, list) and tags:
            lead.set(Field.TECH_STACK, FieldValue(list(tags), provider=self.name, confidence=1.0))
        job_types = data.get("job_types")
        if isinstance(job_types, list) and job_types:
            lead.set(Field.EMPLOYMENT_TYPE, FieldValue(job_types[0], provider=self.name, confidence=0.8))
        created = data.get("created_at")
        published = _parse_timestamp(created)
        if published is not None:
            lead.set(Field.PUBLISHED_AT, FieldValue(published.isoformat(), provider=self.name, confidence=1.0))
        return lead


def _set(lead: Lead, provider: str, field_name: str, value: Any) -> None:
    if value not in (None, ""):
        lead.set(field_name, FieldValue(value, provider=provider, confidence=1.0))


def _job_text(entry: dict[str, Any]) -> str:
    """The posting's own text: description if present, plain title otherwise.

    ``description`` arrives HTML-escaped a second time on top of the HTML itself
    (arbeitnow embeds ``&lt;p&gt;...`` as a JSON string), so it is unescaped once
    before the normal HTML-to-text pass runs.
    """
    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        return _htmltools.html_to_text(html.unescape(description))
    title = entry.get("title")
    return str(title).strip() if isinstance(title, str) else ""


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
