"""Deterministic stand-in for tests.

No test in this package talks to a real model. Where a stage needs an LLM, it gets
this one: it answers from a script, records every prompt it saw, and never opens a
socket or a process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..errors import LLMError

__all__ = ["FakeLLM"]

DEFAULT_ANSWER = "(Attrappe ohne hinterlegte Antwort)"


@dataclass
class FakeLLM:
    """Answers deterministically, in this order of precedence.

    1. ``handler`` - a callable ``(prompt, system) -> str``.
    2. ``rules`` - the first key that occurs in the prompt wins.
    3. ``answers`` - consumed in order; the last one repeats afterwards.
    4. :data:`DEFAULT_ANSWER`.

    ``fail_with`` makes every call raise, to test the behaviour of a stage when the
    provider is down.
    """

    answers: Sequence[str] = field(default_factory=tuple)
    rules: Mapping[str, str] = field(default_factory=dict)
    handler: Callable[[str, str | None], str] | None = None
    fail_with: str | None = None
    label: str = "fake"
    prompts: list[str] = field(default_factory=list, init=False)
    systems: list[str | None] = field(default_factory=list, init=False)
    calls: int = field(default=0, init=False)

    @property
    def name(self) -> str:
        return f"fake:{self.label}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.systems.append(system)
        index = self.calls
        self.calls += 1
        if self.fail_with:
            raise LLMError(self.fail_with)
        if self.handler is not None:
            return self.handler(prompt, system)
        for needle, answer in self.rules.items():
            if needle in prompt:
                return answer
        if self.answers:
            return self.answers[min(index, len(self.answers) - 1)]
        return DEFAULT_ANSWER

    def available(self) -> bool:
        return self.fail_with is None
