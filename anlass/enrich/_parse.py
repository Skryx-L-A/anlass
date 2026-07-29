"""Tolerant JSON extraction from model output.

Deliberately a small duplicate of :mod:`anlass.draft._parse` rather than an import
from it: stage 3 (enrichment) has no business depending on stage 6/7's private
module, and the function is fifteen lines. Two independent copies of a stable
helper are cheaper than a package-layering violation.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["extract_json"]


def extract_json(raw: str) -> dict[str, Any] | None:
    """Return the JSON object contained in ``raw``, or ``None``."""
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        decoded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None
