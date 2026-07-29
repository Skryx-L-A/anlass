"""Model binding: three provider types behind one interface, plus a test stand-in.

* :class:`~anlass.llm.local.OllamaLLM` - local, default, no key.
* :class:`~anlass.llm.apikey.AnthropicLLM` / :class:`~anlass.llm.apikey.OpenAILLM` -
  cloud, paid per token, key from the environment.
* :class:`~anlass.llm.subscription.SubscriptionCLI` - cloud through an already
  installed agent CLI, driven headless, so an existing subscription is used instead of
  paying per token.
* :class:`~anlass.llm.fake.FakeLLM` - deterministic, for tests.

The choice is made per stage, see :mod:`anlass.llm.router`.
"""

from __future__ import annotations

from .apikey import AnthropicLLM, OpenAILLM
from .fake import FakeLLM
from .local import OllamaLLM
from .router import LLMRouter, Stage, build_llm, build_router
from .subscription import SubscriptionCLI

__all__ = [
    "AnthropicLLM",
    "FakeLLM",
    "LLMRouter",
    "OllamaLLM",
    "OpenAILLM",
    "Stage",
    "SubscriptionCLI",
    "build_llm",
    "build_router",
]
