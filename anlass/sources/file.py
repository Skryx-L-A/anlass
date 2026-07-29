"""Stage 1: read raw records out of a local JSON or CSV file.

JSON is either a top-level list of objects, or an object with the list under
``records``/``items``/``jobs``/``data``. CSV is a header row plus one record per
row. Both are treated the same past that point: every column/key is matched
against a small alias table onto :class:`anlass.models.Field`, case-insensitively,
so ``"company"`` and ``"Unternehmen"`` both land on ``Field.ORGANIZATION``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..errors import SourceError
from ..models import Field, FieldValue, Lead, RawRecord

__all__ = ["FileSource"]

#: Field name to the column/key names a file may use for it, checked lower-cased.
_ALIASES: dict[str, tuple[str, ...]] = {
    Field.ORGANIZATION: ("organization", "organisation", "company", "employer", "unternehmen", "firma"),
    Field.ROLE: ("role", "title", "position", "job_title", "stelle", "stellenbezeichnung"),
    Field.URL: ("url", "link", "href"),
    Field.LOCATION: ("location", "ort", "city", "standort"),
    Field.REMOTE: ("remote", "home_office", "homeoffice"),
    Field.EMPLOYMENT_TYPE: ("employment_type", "vertragsform", "contract_type"),
    Field.WORKLOAD_HOURS: ("workload_hours", "hours", "stunden", "wochenstunden"),
    Field.HEADCOUNT: ("headcount", "employees", "mitarbeiterzahl", "team_size"),
    Field.TECH_STACK: ("tech_stack", "technologies", "stack", "technologien"),
    Field.REQUIRED_SKILLS: ("required_skills", "skills", "requirements", "anforderungen"),
    Field.CONTACT_NAME: ("contact_name", "contact", "ansprechpartner", "ansprechpartnerin"),
    Field.CONTACT_EMAIL: ("contact_email", "email", "mail", "e-mail"),
    Field.PUBLISHED_AT: ("published_at", "date", "posted_at", "veroeffentlicht"),
}
_BOOL_FIELDS = frozenset({Field.REMOTE})
_INT_FIELDS = frozenset({Field.WORKLOAD_HOURS, Field.HEADCOUNT})
_LIST_FIELDS = frozenset({Field.TECH_STACK, Field.REQUIRED_SKILLS})
_TRUE = {"true", "yes", "ja", "1", "remote", "home office", "homeoffice"}
_FALSE = {"false", "no", "nein", "0"}
_TEXT_KEYS = ("text", "description", "beschreibung", "body", "inhalt")


def _coerce_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return None


def _coerce_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _coerce_list(raw: Any) -> list[str] | None:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",") if item.strip()]
        return items or None
    return None


def _coerce(field_name: str, raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if field_name in _BOOL_FIELDS:
        return _coerce_bool(raw)
    if field_name in _INT_FIELDS:
        return _coerce_int(raw)
    if field_name in _LIST_FIELDS:
        return _coerce_list(raw)
    return raw


@dataclass
class FileSource:
    """Reads a JSON or CSV file. Format from the extension, override with ``format``.

    Cannot filter by ``since``: a file has no reliable notion of "already seen",
    every call yields the whole file and lets ``limit`` cap it.
    """

    path: str | Path
    format: str = "auto"

    @property
    def name(self) -> str:
        return "file"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        path = Path(self.path)
        if not path.is_file():
            raise SourceError(f"Die Datei '{path}' gibt es nicht.")
        fmt = self._resolve_format(path)
        rows = self._read_json(path) if fmt == "json" else self._read_csv(path)
        count = 0
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise SourceError(f"Eintrag {index + 1} in '{path}' ist kein Objekt.")
            lowered = {str(key).strip().lower(): value for key, value in row.items()}
            text = self._extract_text(lowered)
            external_id = str(row.get("id") or row.get("external_id") or f"{path.name}:{index}")
            record_url = lowered.get("url") or lowered.get("link")
            yield RawRecord(
                source=self.name,
                external_id=external_id,
                text=text,
                url=str(record_url) if record_url else None,
                data=dict(row),
            )
            count += 1
            if limit is not None and count >= limit:
                return

    def normalize(self, record: RawRecord) -> Lead:
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        lowered = {str(key).strip().lower(): value for key, value in record.data.items()}
        for field_name, keys in _ALIASES.items():
            for key in keys:
                if key not in lowered:
                    continue
                value = _coerce(field_name, lowered[key])
                if value is None:
                    continue
                lead.set(field_name, FieldValue(value, provider=self.name, confidence=1.0))
                break
        if record.url and Field.URL not in lead.fields:
            lead.set(Field.URL, FieldValue(record.url, provider=self.name, confidence=1.0))
        return lead

    def _extract_text(self, lowered: Mapping[str, Any]) -> str:
        for key in _TEXT_KEYS:
            value = lowered.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        parts = [str(lowered[k]).strip() for k in ("title", "role", "organization", "company") if lowered.get(k)]
        return " - ".join(parts)

    def _resolve_format(self, path: Path) -> str:
        if self.format != "auto":
            return self.format
        suffix = path.suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
        raise SourceError(f"Kann das Format von '{path}' nicht an der Endung '{suffix}' erkennen.")

    def _read_json(self, path: Path) -> Sequence[Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError(f"Die Datei '{path}' ist kein gueltiges JSON: {exc}") from exc
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("records", "items", "jobs", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
        raise SourceError(f"Die Datei '{path}' enthaelt keine erkennbare Liste von Eintraegen.")

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
