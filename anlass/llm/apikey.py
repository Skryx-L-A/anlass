"""Cloud models over their HTTP APIs, paid per token.

Keys are read from the environment at call time and never from a file in the repo,
never cached on the instance, never written to a log or an error message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..errors import LLMError
from . import _http

__all__ = ["AnthropicLLM", "OpenAILLM"]


def _read_key(variable: str, provider: str) -> str:
    key = os.environ.get(variable, "").strip()
    if not key:
        raise LLMError(
            f"Fuer {provider} ist kein Schluessel in der Umgebung gesetzt "
            f"(erwartet in der Variablen {variable})."
        )
    return key


@dataclass
class AnthropicLLM:
    """Anthropic Messages API."""

    model: str = "claude-sonnet-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"
    timeout: float = 120.0

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": _read_key(self.api_key_env, "Anthropic"),
            "anthropic-version": self.api_version,
        }
        data = _http.post_json(
            f"{self.base_url.rstrip('/')}/v1/messages", payload, headers=headers, timeout=self.timeout
        )
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError("Anthropic antwortete ohne Inhaltsblock.")
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        if not texts:
            raise LLMError("Anthropic antwortete ohne Textblock.")
        return "".join(texts).strip()

    def available(self) -> bool:
        return bool(os.environ.get(self.api_key_env, "").strip())


@dataclass
class OpenAILLM:
    """OpenAI chat completions API. Also works against compatible endpoints."""

    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com"
    timeout: float = 120.0

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {_read_key(self.api_key_env, 'OpenAI')}"}
        data = _http.post_json(
            f"{self.base_url.rstrip('/')}/v1/chat/completions",
            payload,
            headers=headers,
            timeout=self.timeout,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("OpenAI antwortete ohne Auswahl.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError("OpenAI antwortete ohne Textinhalt.")
        return content.strip()

    def available(self) -> bool:
        return bool(os.environ.get(self.api_key_env, "").strip())
