"""The registry: name a source, get its class; build one from a config block."""

from __future__ import annotations

import pytest

from anlass import sources
from anlass.errors import ConfigError
from anlass.interfaces import Source
from anlass.sources.rss import RssSource
from anlass.sources.url import UrlSource


@pytest.mark.parametrize("name", ["file", "url", "rss", "careerpage", "openapi"])
def test_every_shipped_source_is_registered_and_conforms(name):
    cls = sources.get(name)
    assert issubclass(cls, object)
    instance = cls.__new__(cls)  # protocol check does not need __init__
    assert isinstance(instance, Source)


def test_get_is_case_and_whitespace_tolerant():
    assert sources.get(" URL ") is UrlSource


def test_get_reports_an_unknown_name():
    with pytest.raises(ConfigError) as excinfo:
        sources.get("gibt-es-nicht")
    assert "gibt-es-nicht" in str(excinfo.value)


def test_register_adds_a_custom_source():
    class OwnSource:
        @property
        def name(self) -> str:
            return "eigen"

        def fetch(self, *, since=None, limit=None):
            return iter(())

        def normalize(self, record):
            raise NotImplementedError

    sources.register("eigen", OwnSource)
    try:
        assert sources.get("eigen") is OwnSource
    finally:
        del sources.SOURCES["eigen"]


def test_build_source_constructs_with_the_type_key_removed():
    built = sources.build_source({"type": "rss", "url": "https://beispiel.example/feed.xml"})
    assert isinstance(built, RssSource)
    assert built.url == "https://beispiel.example/feed.xml"


def test_build_source_needs_a_type():
    with pytest.raises(ConfigError):
        sources.build_source({"url": "https://beispiel.example"})


def test_build_source_reports_unpassende_angaben():
    with pytest.raises(ConfigError):
        sources.build_source({"type": "rss", "unbekannt": 1})


def test_build_source_rejects_a_non_mapping():
    with pytest.raises(ConfigError):
        sources.build_source("rss")  # type: ignore[arg-type]
