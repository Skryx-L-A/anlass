"""Loading the user's profile: fact base, voice, criteria.

The profile is data, not code, and it stays out of the repository - ``profile/`` is
ignored, only ``profile.example/`` ships.

The criteria file is loaded raw on purpose: its semantics belong to stage 4, which is
phase 2. Reading it here would fix an evaluation model before the scorer exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .models import Fact

__all__ = ["Profile", "load_facts", "load_profile", "load_voice"]


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"Die Datei '{path}' gibt es nicht.")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Die Datei '{path}' ist kein gueltiges YAML: {exc}") from exc


def load_facts(path: str | Path) -> list[Fact]:
    """Read a fact base. Every entry needs id, claim and source.

    Raises:
        anlass.errors.ConfigError: File missing, malformed, entry incomplete, or an id
            used twice - duplicate ids would make citations ambiguous.
    """
    path = Path(path)
    data = _read_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
        raise ConfigError(f"In '{path}' fehlt die Liste 'facts'.")
    facts: list[Fact] = []
    seen: set[str] = set()
    for index, entry in enumerate(data["facts"], start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"Eintrag {index} in '{path}' ist kein Objekt.")
        missing = [key for key in ("id", "claim", "source") if not str(entry.get(key, "")).strip()]
        if missing:
            raise ConfigError(
                f"Eintrag {index} in '{path}' fehlt: {', '.join(missing)}. "
                "Jeder Fakt braucht Kennung, Aussage und Fundstelle."
            )
        fact_id = str(entry["id"]).strip()
        if fact_id in seen:
            raise ConfigError(f"Die Kennung '{fact_id}' kommt in '{path}' mehrfach vor.")
        seen.add(fact_id)
        facts.append(
            Fact(id=fact_id, claim=str(entry["claim"]).strip(), source=str(entry["source"]).strip())
        )
    if not facts:
        raise ConfigError(f"Die Faktenbasis '{path}' ist leer.")
    return facts


def load_voice(path: str | Path) -> str:
    """Read the voice description. Missing file is not an error - voice is optional."""
    path = Path(path)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


@dataclass
class Profile:
    """A loaded profile directory."""

    facts: list[Fact]
    voice: str = ""
    criteria: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def fact(self, fact_id: str) -> Fact | None:
        for entry in self.facts:
            if entry.id == fact_id:
                return entry
        return None


def load_profile(directory: str | Path) -> Profile:
    """Load ``facts.yaml``, ``voice.md`` and ``criteria.yaml`` from a directory."""
    path = Path(directory)
    if not path.is_dir():
        raise ConfigError(f"Das Profilverzeichnis '{path}' gibt es nicht.")
    criteria_path = path / "criteria.yaml"
    criteria = _read_yaml(criteria_path) if criteria_path.is_file() else {}
    if criteria is None:
        criteria = {}
    if not isinstance(criteria, dict):
        raise ConfigError(f"Die Datei '{criteria_path}' muss ein Objekt enthalten.")
    return Profile(
        facts=load_facts(path / "facts.yaml"),
        voice=load_voice(path / "voice.md"),
        criteria=criteria,
        path=path,
    )
