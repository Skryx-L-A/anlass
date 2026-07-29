"""anlass - grounded outbound engine.

Three rules live in the code, not in the README:

1. No contact without an occasion (``Signal``) that can be quoted from the source.
2. No claim without evidence: every factual statement in a generated text must point
   at a ``Fact`` of the user's fact base. Unsupported statements are caught by
   machine, not hoped away.
3. Nothing leaves without approval, and there is no switch that turns that off.

Code, identifiers and docstrings are English; everything a user reads is German.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .models import (
    ApprovalRecord,
    ApprovalState,
    Draft,
    Fact,
    FieldValue,
    Finding,
    FindingKind,
    Lead,
    Paragraph,
    Severity,
    Signal,
    VerificationResult,
)

__all__ = [
    "__version__",
    "ApprovalRecord",
    "ApprovalState",
    "Draft",
    "Fact",
    "FieldValue",
    "Finding",
    "FindingKind",
    "Lead",
    "Paragraph",
    "Severity",
    "Signal",
    "VerificationResult",
]
