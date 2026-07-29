"""Stage 8: approval as a state transition, and the anti-mass rule that guards it."""

from __future__ import annotations

from .approve import ApprovalError, approve, mark_sent, reject, transition
from .gate import RuleGate
from .limits import (
    InMemorySendLog,
    LimitError,
    SendLimits,
    SendLog,
    StoreSendLog,
    enforce,
    limit_violations,
)

__all__ = [
    "ApprovalError",
    "InMemorySendLog",
    "LimitError",
    "RuleGate",
    "SendLimits",
    "SendLog",
    "StoreSendLog",
    "approve",
    "enforce",
    "limit_violations",
    "mark_sent",
    "reject",
    "transition",
]
