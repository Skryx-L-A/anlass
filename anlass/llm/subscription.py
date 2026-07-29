"""Cloud models through an already installed agent CLI, driven headless.

Whoever pays for a subscription anyway pays nothing extra. The call is configured,
not nailed to a product: any CLI that reads a prompt and prints an answer works.

Two ways to hand the prompt over:

* ``prompt_via="stdin"`` (default) - the prompt goes to standard input. Works with
  every CLI that reads a piped prompt and needs no quoting.
* ``prompt_via="argument"`` - the placeholders ``{prompt}`` and ``{system}`` in
  ``command`` are replaced. Without a ``{prompt}`` placeholder the prompt is appended
  as the last argument.

Example configuration (YAML)::

    llm:
      default:
        provider: subscription
        command: ["meine-agenten-cli", "--print", "{prompt}"]
        prompt_via: argument
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..errors import LLMError

__all__ = ["SubscriptionCLI"]

PROMPT_PLACEHOLDER = "{prompt}"
SYSTEM_PLACEHOLDER = "{system}"


@dataclass
class SubscriptionCLI:
    """Runs a configured command and returns its standard output.

    Args:
        command: argv of the CLI. The first element must be on PATH or an absolute path.
        prompt_via: ``"stdin"`` or ``"argument"``.
        label: Name shown in provenance. Defaults to the command name.
        timeout: Seconds to wait for the process.
        env: Extra environment variables for the child process.
        cwd: Working directory for the child process.
    """

    command: Sequence[str]
    prompt_via: str = "stdin"
    label: str = ""
    timeout: float = 300.0
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("Fuer den Abo-Anbieter fehlt der aufzurufende Befehl.")
        if self.prompt_via not in ("stdin", "argument"):
            raise ValueError("prompt_via muss 'stdin' oder 'argument' sein.")

    @property
    def name(self) -> str:
        return f"subscription:{self.label or self.command[0]}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Run the CLI once.

        ``temperature`` and ``max_tokens`` are ignored: a foreign CLI has its own
        settings and inventing flags for it would nail this down to one product.
        """
        argv, stdin_text = self._build_call(prompt, system)
        environment = None
        if self.env:
            import os

            environment = {**os.environ, **self.env}
        try:
            completed = subprocess.run(  # noqa: S603 - argv is configured by the user
                list(argv),
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
                env=environment,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LLMError(f"Der konfigurierte Befehl '{argv[0]}' wurde nicht gefunden.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"Der konfigurierte Befehl '{argv[0]}' antwortete nicht innerhalb von "
                f"{self.timeout:.0f} Sekunden."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[-500:]
            raise LLMError(
                f"Der konfigurierte Befehl '{argv[0]}' endete mit Rueckgabewert "
                f"{completed.returncode}: {detail}"
            )
        answer = (completed.stdout or "").strip()
        if not answer:
            raise LLMError(f"Der konfigurierte Befehl '{argv[0]}' lieferte keine Ausgabe.")
        return answer

    def available(self) -> bool:
        return shutil.which(self.command[0]) is not None

    def _build_call(self, prompt: str, system: str | None) -> tuple[list[str], str | None]:
        """Return argv and stdin text for one call. Exposed for tests."""
        if self.prompt_via == "stdin":
            takes_system = any(SYSTEM_PLACEHOLDER in part for part in self.command)
            argv = [part.replace(SYSTEM_PLACEHOLDER, system or "") for part in self.command]
            if system and not takes_system:
                return argv, f"{system}\n\n{prompt}"
            return argv, prompt
        argv = []
        used_placeholder = False
        for part in self.command:
            if PROMPT_PLACEHOLDER in part:
                part = part.replace(PROMPT_PLACEHOLDER, prompt)
                used_placeholder = True
            part = part.replace(SYSTEM_PLACEHOLDER, system or "")
            argv.append(part)
        if not used_placeholder:
            argv.append(f"{system}\n\n{prompt}" if system else prompt)
        return argv, None
