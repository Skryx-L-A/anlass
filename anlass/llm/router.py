"""Model choice per stage, not globally.

Extraction and classification are mechanical and run well locally; drafting and
verification need more. So the default runs stages 3 and 10 locally and leaves the
choice open for stages 6 and 7: normal operation costs nothing and the expensive stage
is the one that is worth it.

Configuration shape (as it appears in the config file)::

    llm:
      default:
        provider: ollama
        model: qwen3:8b
      stages:
        draft:
          provider: anthropic
          model: claude-sonnet-5
        verify:
          provider: subscription
          command: ["meine-agenten-cli", "--print"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..errors import ConfigError
from ..interfaces import LLM
from .apikey import AnthropicLLM, OpenAILLM
from .fake import FakeLLM
from .local import OllamaLLM
from .subscription import SubscriptionCLI

__all__ = ["LLMRouter", "Stage", "build_llm", "build_router"]


class Stage(str, Enum):
    """The pipeline stages that use a language model at all."""

    ENRICH = "enrich"
    SIGNAL = "signal"
    DRAFT = "draft"
    VERIFY = "verify"
    TRACK = "track"


PROVIDERS: dict[str, type] = {
    "ollama": OllamaLLM,
    "local": OllamaLLM,
    "anthropic": AnthropicLLM,
    "openai": OpenAILLM,
    "subscription": SubscriptionCLI,
    "fake": FakeLLM,
}


def build_llm(spec: Mapping[str, Any]) -> LLM:
    """Build one provider from its configuration block.

    Args:
        spec: Must contain ``provider``; every other key is passed to the provider
            class unchanged.

    Raises:
        anlass.errors.ConfigError: Unknown provider or unusable arguments.
    """
    if not isinstance(spec, Mapping):
        raise ConfigError("Ein Modellblock muss eine Zuordnung sein.")
    provider = str(spec.get("provider", "")).strip().lower()
    if not provider:
        raise ConfigError("Im Modellblock fehlt der Eintrag 'provider'.")
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ConfigError(f"Unbekannter Modellanbieter '{provider}'. Moeglich sind: {known}.")
    kwargs = {key: value for key, value in spec.items() if key != "provider"}
    try:
        return PROVIDERS[provider](**kwargs)
    except TypeError as exc:
        raise ConfigError(f"Der Modellblock fuer '{provider}' hat unpassende Angaben: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"Der Modellblock fuer '{provider}' ist ungueltig: {exc}") from exc


@dataclass
class LLMRouter:
    """Hands out the model configured for a stage, or the default."""

    default: LLM
    per_stage: dict[Stage, LLM] = field(default_factory=dict)

    def for_stage(self, stage: Stage | str) -> LLM:
        """Model for ``stage``. Falls back to the default."""
        key = Stage(stage) if not isinstance(stage, Stage) else stage
        return self.per_stage.get(key, self.default)

    def describe(self) -> dict[str, str]:
        """Stage to model name, for ``anlass init`` and for the log."""
        mapping = {stage.value: self.for_stage(stage).name for stage in Stage}
        mapping["default"] = self.default.name
        return mapping


def build_router(config: Mapping[str, Any]) -> LLMRouter:
    """Build a router from the ``llm`` section of the configuration.

    Raises:
        anlass.errors.ConfigError: The section is missing, or a stage name is unknown.
    """
    if "default" not in config:
        raise ConfigError("Im Abschnitt 'llm' fehlt der Block 'default'.")
    default = build_llm(config["default"])
    per_stage: dict[Stage, LLM] = {}
    stages = config.get("stages") or {}
    if not isinstance(stages, Mapping):
        raise ConfigError("Der Eintrag 'llm.stages' muss eine Zuordnung sein.")
    for raw_stage, spec in stages.items():
        try:
            stage = Stage(str(raw_stage).strip().lower())
        except ValueError as exc:
            known = ", ".join(s.value for s in Stage)
            raise ConfigError(
                f"Unbekannte Stufe '{raw_stage}' in 'llm.stages'. Moeglich sind: {known}."
            ) from exc
        per_stage[stage] = build_llm(spec)
    return LLMRouter(default=default, per_stage=per_stage)
