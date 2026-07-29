"""Where things live, and how they are read and written.

One directory holds everything a run needs: configuration, the user's profile, the
database and the outbox. Its location comes from ``--config``, from the environment
variable ``ANLASS_HOME``, or from the usual place for configuration on this system.

Nothing here is overwritten without a copy being put aside first. The rule holds for
the configuration and for every file of the profile skeleton: a second ``anlass init``
must not be able to cost anyone their fact base.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..errors import ConfigError

__all__ = [
    "CONFIG_NAME",
    "HOME_ENV",
    "Paths",
    "backup_file",
    "load_config",
    "resolve_paths",
    "save_config",
]

HOME_ENV = "ANLASS_HOME"
CONFIG_NAME = "config.yaml"
CONFIG_VERSION = 1


@dataclass(frozen=True, slots=True)
class Paths:
    """Every path one installation uses. Derived from the home directory."""

    home: Path

    @property
    def config(self) -> Path:
        return self.home / CONFIG_NAME

    @property
    def profile(self) -> Path:
        return self.home / "profile"

    @property
    def database(self) -> Path:
        return self.home / "anlass.db"

    @property
    def outbox(self) -> Path:
        return self.home / "ausgang"

    def lines(self) -> list[str]:
        """German overview for the terminal."""
        return [
            f"Verzeichnis:   {self.home}",
            f"Konfiguration: {self.config}",
            f"Profil:        {self.profile}",
            f"Datenbank:     {self.database}",
            f"Ausgang:       {self.outbox}",
        ]


def resolve_paths(explicit: str | Path | None = None) -> Paths:
    """Home directory of this installation.

    Args:
        explicit: What ``--config`` said. A directory is taken as the home directory, a
            file as the configuration inside it.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        home = candidate.parent if candidate.suffix in (".yaml", ".yml") else candidate
        return Paths(home=home)
    from_env = os.environ.get(HOME_ENV, "").strip()
    if from_env:
        return Paths(home=Path(from_env).expanduser())
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return Paths(home=root / "anlass")


def backup_file(path: Path) -> Path | None:
    """Put a copy of ``path`` aside. Returns the copy, or ``None`` if there was nothing.

    The name carries a UTC timestamp, so repeated runs pile up instead of overwriting
    each other - which is the whole point of a backup.
    """
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 2
    while target.exists():
        target = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, target)
    return target


def load_config(paths: Paths) -> dict[str, Any]:
    """Read the configuration. A missing file yields an empty mapping, not an error.

    Raises:
        anlass.errors.ConfigError: The file exists but is not a YAML mapping.
    """
    if not paths.config.is_file():
        return {}
    try:
        data = yaml.safe_load(paths.config.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Die Konfiguration '{paths.config}' ist kein gueltiges YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Die Konfiguration '{paths.config}' muss ein Objekt enthalten.")
    return data


def save_config(paths: Paths, config: Mapping[str, Any]) -> Path | None:
    """Write the configuration, after putting an existing one aside.

    Returns:
        The backup that was made, or ``None`` if there was nothing to back up.
    """
    paths.home.mkdir(parents=True, exist_ok=True)
    saved = backup_file(paths.config)
    body = yaml.safe_dump(dict(config), allow_unicode=True, sort_keys=False)
    paths.config.write_text(_HEADER + body, encoding="utf-8")
    return saved


_HEADER = """# Konfiguration von anlass. Von 'anlass init' geschrieben, von Hand aenderbar.
#
# Hier stehen keine Zugangsdaten. Wo ein Schluessel oder ein Passwort noetig ist,
# steht nur der Name der Umgebungsvariablen, aus der er zur Laufzeit gelesen wird.
"""
