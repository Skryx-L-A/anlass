"""Schedule a follow-up. Set a due date, never send.

There is deliberately no ``send`` anywhere in this module - not a flag that defaults to
off, an absent method. A follow-up becoming due is a fact you can query; turning it into
an outgoing message is a separate, explicit step through :mod:`anlass.transport` that a
human or a later, clearly-labelled command triggers.

:class:`FollowUp` itself now lives in :mod:`anlass.models`, because it is persisted:
``Store.save_follow_up`` and ``Store.list_follow_ups`` keep entries across program runs,
which is what a due date needs to be worth anything. :class:`FollowUpBook` stays as the
in-memory variant for a caller that wants to schedule without a store - the pipeline
uses the store (:meth:`anlass.pipeline.Pipeline.schedule_follow_ups`).

The default distance is a decision and not a law: :data:`DEFAULT_FOLLOW_UP_AFTER` is
seven days, long enough that a nudge is not noise and short enough that a thread is not
cold. It is one number in one place, so changing it is one edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import FollowUp, utcnow

__all__ = ["DEFAULT_FOLLOW_UP_AFTER", "FollowUp", "FollowUpBook"]

#: How long silence has to last before a draft is worth a nudge.
DEFAULT_FOLLOW_UP_AFTER = timedelta(days=7)


@dataclass
class FollowUpBook:
    """In-memory collection of scheduled follow-ups."""

    _entries: list[FollowUp] = field(default_factory=list, init=False, repr=False)

    def schedule(self, draft_id: str, *, after: timedelta, reason: str = "") -> FollowUp:
        """Due ``after`` from now. Returns the entry so the caller can store its id."""
        entry = FollowUp(draft_id=draft_id, due_at=utcnow() + after, reason=reason)
        self._entries.append(entry)
        return entry

    def due(self, *, now: datetime | None = None) -> list[FollowUp]:
        """Every entry due at or before ``now`` (default: this moment)."""
        cutoff = now or utcnow()
        return [entry for entry in self._entries if entry.due_at <= cutoff]

    def all(self) -> list[FollowUp]:
        return list(self._entries)
