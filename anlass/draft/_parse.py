"""Tolerant JSON extraction from model output.

Models wrap JSON in prose or code fences no matter how the prompt is worded. This
recovers the object without accepting arbitrary garbage: it takes the outermost brace
pair and parses it strictly.
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
