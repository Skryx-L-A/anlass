"""Local models over Ollama's HTTP API.

The default provider: free, no key, data stays on the machine. That is a real
distinguishing feature in this field and it matches the local-first rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import LLMError
from . import _http

__all__ = ["OllamaLLM"]

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"


@dataclass
class OllamaLLM:
    """Talks to a running Ollama instance.

    Args:
        model: Model tag as Ollama knows it.
        host: Base URL of the Ollama server.
        timeout: Seconds to wait for a completion.
    """

    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    timeout: float = 120.0

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        options: dict[str, object] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        data = _http.post_json(f"{self.host.rstrip('/')}/api/generate", payload, timeout=self.timeout)
        answer = data.get("response")
        if not isinstance(answer, str):
            raise LLMError(f"Ollama antwortete ohne Textfeld 'response' (Modell {self.model}).")
        # A model with a reasoning step splits its output: the deliberation goes to
        # 'thinking', the answer to 'response'. Hit the token ceiling mid-deliberation
        # and 'response' arrives empty - measured on 2026-07-29, where the hardest
        # verification case spent all 1200 tokens thinking. Saying so beats "no usable
        # answer", which sends the reader looking for a fault in the prompt.
        if not answer.strip() and data.get("done_reason") == "length":
            thought = data.get("thinking")
            spent = len(thought) if isinstance(thought, str) else 0
            raise LLMError(
                f"Ollama brach die Antwort am Token-Limit ab, bevor sie begann (Modell "
                f"{self.model}): der Denkschritt verbrauchte das Budget"
                + (f" ({spent} Zeichen Denktext)" if spent else "")
                + ". Hoeheres max_tokens setzen - den Denkschritt abzuschalten ist keine "
                "Loesung, das erzeugt Fehlalarme."
            )
        return answer.strip()

    def available(self) -> bool:
        try:
            data = _http.get_json(f"{self.host.rstrip('/')}/api/tags", timeout=5.0)
        except Exception:
            return False
        models = data.get("models")
        if not isinstance(models, list):
            return False
        return any(isinstance(entry, dict) and entry.get("name") == self.model for entry in models)
