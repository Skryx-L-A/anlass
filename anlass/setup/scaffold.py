"""Creating the directory: configuration, fact base skeleton, criteria template.

Idempotent in the only sense that counts here: a second run may not destroy anything.
An existing file is left alone unless it is explicitly to be replaced, and even then a
timestamped copy is put aside first. So running ``anlass init`` twice by accident costs
a moment, never a fact base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import Paths, backup_file, save_config
from .templates import CRITERIA_TEMPLATE, FACTS_SKELETON, SOURCES_SKELETON, VOICE_TEMPLATE

__all__ = ["ScaffoldResult", "scaffold"]

_FILES = {
    "facts.yaml": FACTS_SKELETON,
    "criteria.yaml": CRITERIA_TEMPLATE,
    "sources.yaml": SOURCES_SKELETON,
    "voice.md": VOICE_TEMPLATE,
}


@dataclass
class ScaffoldResult:
    """What the scaffolding did, file by file, so the run can report it truthfully."""

    created: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    backed_up: list[Path] = field(default_factory=list)

    def lines(self) -> list[str]:
        """German report of what happened."""
        out = [f"angelegt:    {path}" for path in self.created]
        out += [f"unberuehrt:  {path}" for path in self.kept]
        out += [f"gesichert:   {path}" for path in self.backed_up]
        return out


def scaffold(
    paths: Paths, config: Mapping[str, Any], *, replace_profile: bool = False
) -> ScaffoldResult:
    """Write configuration and profile skeleton.

    Args:
        paths: Where everything goes.
        config: The configuration the interview produced.
        replace_profile: Overwrite existing profile files with the templates again. The
            old file is copied aside first either way.
    """
    result = ScaffoldResult()

    saved = save_config(paths, config)
    if saved is not None:
        result.backed_up.append(saved)
    result.created.append(paths.config)

    profile_dir = paths.home / str(config.get("profile", {}).get("path", "profile"))
    profile_dir.mkdir(parents=True, exist_ok=True)
    for name, body in _FILES.items():
        target = profile_dir / name
        if target.exists() and not replace_profile:
            result.kept.append(target)
            continue
        copy = backup_file(target)
        if copy is not None:
            result.backed_up.append(copy)
        target.write_text(body, encoding="utf-8")
        result.created.append(target)

    mailbox = config.get("mailbox", {})
    if isinstance(mailbox, Mapping) and mailbox.get("transport") == "datei":
        outbox = paths.home / str(mailbox.get("directory", "ausgang"))
        outbox.mkdir(parents=True, exist_ok=True)
        result.created.append(outbox)

    return result
