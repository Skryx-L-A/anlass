"""Placeholder CLI, profile loading, and the rules that hold for the whole repository."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from anlass import __version__
from anlass.cli import main
from anlass.errors import ConfigError
from anlass.profile import load_facts, load_profile, load_voice

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "profile.example"


# ---------------------------------------------------------------------------- cli

def test_main_without_a_subcommand_shows_what_there_is(capsys):
    """Since phase 2 the CLI has subcommands, so the bare call lists them."""
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "BEFEHL" in out
    for name in ("init", "fetch", "score", "draft", "review", "send", "report"):
        assert name in out


def test_version_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "anlass", "--version"], capture_output=True, text=True, cwd=REPO
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == f"anlass {__version__}"


def test_help_is_german():
    completed = subprocess.run(
        [sys.executable, "-m", "anlass", "--help"], capture_output=True, text=True, cwd=REPO
    )
    assert completed.returncode == 0
    assert "Kein Kontakt ohne Anlass" in completed.stdout


def test_an_unknown_flag_fails_loudly():
    completed = subprocess.run(
        [sys.executable, "-m", "anlass", "--gibtsnicht"], capture_output=True, text=True, cwd=REPO
    )
    assert completed.returncode != 0


# ------------------------------------------------------------------------ profile

def test_the_shipped_example_profile_loads():
    profile = load_profile(EXAMPLE)
    assert len(profile.facts) >= 5
    assert len({f.id for f in profile.facts}) == len(profile.facts)
    assert all(f.claim and f.source for f in profile.facts)
    assert profile.voice
    assert [c["name"] for c in profile.criteria["criteria"]]
    assert profile.criteria["limits"]["min_score"] >= 1
    assert profile.fact("pipeline-latenz") is not None


def test_example_profile_keys_are_english():
    for entry in (EXAMPLE / "facts.yaml").read_text(encoding="utf-8").splitlines():
        if entry.strip().startswith("- id:"):
            break
    else:  # pragma: no cover - only reached if the example file is malformed
        pytest.fail("Die Beispiel-Faktenbasis hat keine englischen Schluessel.")


def test_duplicate_ids_are_refused(tmp_path):
    path = tmp_path / "facts.yaml"
    path.write_text(
        "facts:\n"
        "  - id: a\n    claim: eins\n    source: irgendwo\n"
        "  - id: a\n    claim: zwei\n    source: irgendwo\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_facts(path)
    assert "mehrfach" in str(excinfo.value)


@pytest.mark.parametrize(
    "content",
    [
        "facts:\n  - id: a\n    claim: eins\n",
        "facts:\n  - id: a\n    source: irgendwo\n",
        "fakten:\n  - id: a\n    claim: eins\n    source: irgendwo\n",
        "facts: []\n",
        "- nur eine Liste\n",
    ],
)
def test_incomplete_fact_bases_are_refused(tmp_path, content):
    path = tmp_path / "facts.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_facts(path)


def test_a_missing_file_is_named(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_facts(tmp_path / "gibtsnicht.yaml")
    assert "gibtsnicht.yaml" in str(excinfo.value)


def test_voice_is_optional(tmp_path):
    assert load_voice(tmp_path / "voice.md") == ""


def test_a_missing_profile_directory_is_named(tmp_path):
    with pytest.raises(ConfigError):
        load_profile(tmp_path / "kein-profil")


# --------------------------------------------------------------- repository rules

# Written as escape sequences on purpose: a pattern with literal emoji in it would be
# its own first offender.
_EMOJI = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF\\uFE0F]")


def test_no_emoji_anywhere():
    """The rule says no emojis. A rule nobody checks is a suggestion."""
    offenders = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml", ".toml"}:
            continue
        if any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _EMOJI.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == []


def test_the_own_profile_stays_out_of_the_repository():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "profile/" in ignored
    assert not (REPO / "profile").exists()
