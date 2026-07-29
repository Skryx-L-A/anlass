"""The test call. A configuration that only fails at the first real use is not one.

Two steps, because they fail differently and the user needs to know which one it was:
:meth:`anlass.interfaces.LLM.available` is the cheap reachability check, and one real
completion proves that the provider actually answers with text.

The prompt is deliberately trivial. This measures whether the connection works, not
whether the model is any good - that question belongs to the dry run, where a whole
draft goes through the verification pass.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..errors import AnlassError
from ..interfaces import LLM

__all__ = ["ProbeResult", "probe_llm"]

PROBE_PROMPT = "Antworte mit genau einem Wort: bereit"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What the test call produced. ``ok`` is the only thing a caller must look at."""

    ok: bool
    detail: str
    seconds: float = 0.0
    answer: str = ""

    def line(self) -> str:
        """One German line for the terminal."""
        mark = "OK" if self.ok else "FEHLER"
        return f"Testaufruf {mark} nach {self.seconds:.1f}s: {self.detail}"


def probe_llm(llm: LLM, *, prompt: str = PROBE_PROMPT) -> ProbeResult:
    """Reach the provider once. Never raises - the failure is the result.

    Args:
        llm: The configured provider.
        prompt: What to send. Kept short; this is a connection check.
    """
    started = time.monotonic()
    if not llm.available():
        return ProbeResult(
            ok=False,
            detail=(
                f"'{llm.name}' meldet sich nicht. Bei einem lokalen Modell laeuft "
                "vermutlich kein Ollama oder das Modell ist nicht geladen; bei einem "
                "Anbieter mit Schluessel fehlt die Umgebungsvariable."
            ),
            seconds=time.monotonic() - started,
        )
    try:
        answer = llm.complete(prompt, temperature=0.0, max_tokens=32)
    except AnlassError as exc:
        return ProbeResult(
            ok=False,
            detail=f"'{llm.name}' war erreichbar, antwortete aber nicht: {exc}",
            seconds=time.monotonic() - started,
        )
    except Exception as exc:  # a foreign CLI or library may raise anything
        return ProbeResult(
            ok=False,
            detail=f"'{llm.name}' brach unerwartet ab: {exc}",
            seconds=time.monotonic() - started,
        )
    seconds = time.monotonic() - started
    if not answer.strip():
        return ProbeResult(
            ok=False,
            detail=f"'{llm.name}' antwortete mit einem leeren Text.",
            seconds=seconds,
        )
    return ProbeResult(
        ok=True,
        detail=f"'{llm.name}' antwortete: {answer.strip()[:60]}",
        seconds=seconds,
        answer=answer.strip(),
    )
