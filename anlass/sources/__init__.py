"""Stage 1 and 2: pluggable sources, plus the registry that names them for config.

Shipped sources stay unobjectionable: a file, a single URL, RSS/Atom, a career-page
fetch, and an open job API. There is deliberately no module that scrapes LinkedIn -
it would violate its terms of service and put that liability on every user of a
shared tool, on top of markup that is brittle enough to break without notice. The
:class:`anlass.interfaces.Source` protocol is open regardless: whoever wants that
module writes and registers it themselves with :func:`register`.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ConfigError
from ..interfaces import Source
from .careerpage import CareerPageSource
from .file import FileSource
from .openapi import OpenApiSource
from .rss import RssSource
from .url import UrlSource

__all__ = [
    "SOURCES",
    "CareerPageSource",
    "FileSource",
    "OpenApiSource",
    "RssSource",
    "UrlSource",
    "build_source",
    "get",
    "register",
]

#: Name to source class. A source's own ``name`` property (written into
#: ``Lead.source``) does not have to match its registry key, but the shipped ones do.
SOURCES: dict[str, type] = {
    "file": FileSource,
    "url": UrlSource,
    "rss": RssSource,
    "careerpage": CareerPageSource,
    "openapi": OpenApiSource,
}


def register(name: str, cls: type) -> None:
    """Add or replace a source under ``name`` - how a user's own module joins in."""
    SOURCES[name.strip().lower()] = cls


def get(name: str) -> type:
    """Return the source class registered under ``name``.

    Raises:
        anlass.errors.ConfigError: ``name`` is not registered.
    """
    key = name.strip().lower()
    if key not in SOURCES:
        known = ", ".join(sorted(SOURCES))
        raise ConfigError(f"Unbekannte Quelle '{name}'. Moeglich sind: {known}.")
    return SOURCES[key]


def build_source(spec: Mapping[str, Any]) -> Source:
    """Build one source from a configuration block, e.g. one entry of
    ``profile.example/sources.example.yaml``. Mirrors
    :func:`anlass.llm.router.build_llm` so the two config shapes read the same way.

    Args:
        spec: Must contain ``type``; every other key is passed to the source class.

    Raises:
        anlass.errors.ConfigError: Unknown type or unusable arguments.
    """
    if not isinstance(spec, Mapping):
        raise ConfigError("Ein Quellenblock muss eine Zuordnung sein.")
    kind = str(spec.get("type", "")).strip().lower()
    if not kind:
        raise ConfigError("Im Quellenblock fehlt der Eintrag 'type'.")
    cls = get(kind)
    kwargs = {key: value for key, value in spec.items() if key != "type"}
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"Der Quellenblock fuer '{kind}' hat unpassende Angaben: {exc}") from exc
