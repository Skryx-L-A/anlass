"""The heart: generate from facts (stage 6), verify against facts (stage 7).

The two are separate on purpose. Generation may only see what it is allowed to use;
verification sees the finished text and checks it independently, with its own view of
the fact base and, if configured, a different model.
"""

from __future__ import annotations

from .generate import FactGroundedDrafter, build_prompt
from .verify import DEFAULT_SUPERLATIVES, GroundingVerifier

__all__ = ["DEFAULT_SUPERLATIVES", "FactGroundedDrafter", "GroundingVerifier", "build_prompt"]
