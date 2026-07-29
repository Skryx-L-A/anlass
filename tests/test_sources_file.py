"""FileSource: JSON and CSV, field aliasing, no `since` filtering, `limit` works."""

from __future__ import annotations

import json

import pytest

from anlass.errors import SourceError
from anlass.models import Field
from anlass.sources.file import FileSource


def _write(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_reads_a_top_level_json_list(tmp_path):
    path = _write(
        tmp_path,
        "leads.json",
        json.dumps(
            [
                {
                    "company": "Nordlicht Systeme",
                    "title": "Werkstudent Datenverarbeitung",
                    "url": "https://beispiel.example/1",
                    "text": "Wir bauen unsere Datenverarbeitung selbst.",
                    "remote": "ja",
                }
            ]
        ),
    )
    source = FileSource(path=path)
    records = list(source.fetch())
    assert len(records) == 1
    lead = source.normalize(records[0])
    assert lead.value(Field.ORGANIZATION) == "Nordlicht Systeme"
    assert lead.value(Field.ROLE) == "Werkstudent Datenverarbeitung"
    assert lead.value(Field.REMOTE) is True
    assert lead.provider_of(Field.ORGANIZATION) == "file"
    assert lead.text == "Wir bauen unsere Datenverarbeitung selbst."


def test_reads_a_wrapped_json_object(tmp_path):
    path = _write(tmp_path, "leads.json", json.dumps({"jobs": [{"title": "A"}, {"title": "B"}]}))
    assert len(list(FileSource(path=path).fetch())) == 2


def test_reads_csv_with_a_header_row(tmp_path):
    path = _write(
        tmp_path,
        "leads.csv",
        "company,title,headcount,tech_stack\n"
        "Nordlicht Systeme,Werkstudent,45,Python;Rust\n",
    )
    source = FileSource(path=path)
    records = list(source.fetch())
    lead = source.normalize(records[0])
    assert lead.value(Field.ORGANIZATION) == "Nordlicht Systeme"
    assert lead.value(Field.HEADCOUNT) == 45
    assert lead.value(Field.TECH_STACK) == ["Python;Rust"]


def test_csv_tech_stack_splits_on_commas(tmp_path):
    path = _write(tmp_path, "leads.csv", "title,tech_stack\nA,\"Python, Rust\"\n")
    source = FileSource(path=path)
    lead = source.normalize(next(source.fetch()))
    assert lead.value(Field.TECH_STACK) == ["Python", "Rust"]


def test_missing_file_is_a_source_error(tmp_path):
    with pytest.raises(SourceError):
        list(FileSource(path=tmp_path / "gibt-es-nicht.json").fetch())


def test_unrecognisable_extension_is_a_source_error(tmp_path):
    path = _write(tmp_path, "leads.txt", "x")
    with pytest.raises(SourceError):
        list(FileSource(path=path).fetch())


def test_malformed_json_is_a_source_error(tmp_path):
    path = _write(tmp_path, "leads.json", "{not json")
    with pytest.raises(SourceError):
        list(FileSource(path=path).fetch())


def test_limit_caps_the_yielded_records(tmp_path):
    path = _write(tmp_path, "leads.json", json.dumps([{"title": str(i)} for i in range(5)]))
    assert len(list(FileSource(path=path).fetch(limit=2))) == 2


def test_since_is_a_no_op_a_file_cannot_filter(tmp_path):
    import datetime

    path = _write(tmp_path, "leads.json", json.dumps([{"title": "A"}, {"title": "B"}]))
    records = list(FileSource(path=path).fetch(since=datetime.datetime.now(datetime.timezone.utc)))
    assert len(records) == 2


def test_every_field_carries_confidence_one_verbatim_from_structure(tmp_path):
    path = _write(tmp_path, "leads.json", json.dumps([{"company": "Nordlicht"}]))
    source = FileSource(path=path)
    lead = source.normalize(next(source.fetch()))
    assert lead.fields[Field.ORGANIZATION].confidence == 1.0
