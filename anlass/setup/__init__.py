"""Setup: the interview, the scaffolding, the test call and the dry run.

``anlass init`` is deliberately more than writing a file. It asks four things
(operating system, model provider, mailbox, sources), **proves the model choice with a
real call right away**, writes configuration, fact base skeleton and criteria template,
and then runs the whole chain once against a shipped example posting.

The order matters: a configuration that only fails at the first real use is not a
configuration, and a tool nobody has seen work is one nobody trusts with their own
credentials.

Answers are kept in the configuration file, so a second run offers them as defaults
instead of asking everything again. Nothing is overwritten without a copy being put
aside first.
"""

from __future__ import annotations

from .config import Paths, backup_file, load_config, resolve_paths, save_config
from .interview import (
    Asker,
    ConsoleAsker,
    DefaultAsker,
    ScriptedAsker,
    ask_llm,
    config_from_file,
    config_from_mapping,
    run_interview,
)
from .probe import ProbeResult, probe_llm
from .scaffold import ScaffoldResult, scaffold

__all__ = [
    "Asker",
    "ConsoleAsker",
    "DefaultAsker",
    "Paths",
    "ProbeResult",
    "ScaffoldResult",
    "ScriptedAsker",
    "ask_llm",
    "backup_file",
    "config_from_file",
    "config_from_mapping",
    "load_config",
    "probe_llm",
    "resolve_paths",
    "run_interview",
    "save_config",
    "scaffold",
]
