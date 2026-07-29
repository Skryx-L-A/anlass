"""Stufe 8, die Anti-Massen-Regel - und das ist der Punkt.

Jedes verbreitete Werkzeug in diesem Feld optimiert auf Menge, und die im Plan
zitierten Zahlen sagen, dass genau das nicht funktioniert; eine harte Tagesgrenze, ein
Mindestabstand je Organisation und eine Mindestpunktzahl als Sperre sind deshalb kein
fehlendes Bequemlichkeitsmerkmal, sondern die Bedingung, unter der dieses Werkzeug
ueberhaupt sein Versprechen haelt - ohne sie waere es nur ein weiterer Massenversender
mit besserer Prosa. Es gibt bewusst keinen Schalter, der das abschaltet: keine
``--force``-Option, kein ``--yes-all``, keine Umgebungsvariable, die eine Pruefung hier
uebergeht. Wer die Schwelle aendern will, aendert die Kriteriendatei - das ist eine
bewusste, lesbare und im Zweifel im Diff sichtbare Handlung, kein verstecktes Flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol, runtime_checkable

from ..errors import AnlassError
from ..interfaces import Store
from ..models import ApprovalState, Field, utcnow

__all__ = [
    "InMemorySendLog",
    "LimitError",
    "SendLimits",
    "SendLog",
    "StoreSendLog",
    "enforce",
    "limit_violations",
]


class LimitError(AnlassError):
    """Sending was blocked by a limit. There is no parameter that bypasses this."""


@dataclass(frozen=True, slots=True)
class SendLimits:
    """The three numbers from ``criteria.yaml``'s ``limits`` section."""

    max_sends_per_day: int
    days_between_same_organization: int
    min_score: int

    @classmethod
    def from_criteria(cls, criteria: Mapping[str, Any]) -> "SendLimits":
        """Read the ``limits`` section of a loaded criteria dict.

        Raises:
            anlass.errors.AnlassError: One of the three keys is missing. Falling back
                to a hidden default would make the anti-mass rule depend on a number
                nobody wrote down.
        """
        limits = criteria.get("limits") or {}
        required = ("max_sends_per_day", "days_between_same_organization", "min_score")
        missing = [key for key in required if key not in limits]
        if missing:
            raise AnlassError(
                f"In der Kriteriendatei fehlen die Grenzen: {', '.join(missing)}."
            )
        return cls(
            max_sends_per_day=int(limits["max_sends_per_day"]),
            days_between_same_organization=int(limits["days_between_same_organization"]),
            min_score=int(limits["min_score"]),
        )


def limit_violations(
    limits: SendLimits,
    *,
    score: int | None,
    organization: str | None,
    sent_today: int,
    last_sent_to_organization: datetime | None,
    now: datetime,
) -> list[str]:
    """German reasons the send is blocked, or an empty list if none apply.

    ``score`` of ``None`` means the lead was never scored, and that BLOCKS. Skipping the
    check instead would hand out the bypass this module exists to deny: drop stage 4, pass
    no score, and the minimum-score rule evaporates. An unscored lead is not a
    high-scoring lead, so the only safe reading of "no score" is "not cleared".
    """
    reasons: list[str] = []
    if score is None:
        reasons.append(
            "Lead ist unbewertet - ohne Punktzahl keine Freigabe. "
            "Erst Stufe 4 laufen lassen."
        )
    elif score < limits.min_score:
        reasons.append(f"Punktzahl {score} liegt unter der Mindestpunktzahl {limits.min_score}.")
    if sent_today >= limits.max_sends_per_day:
        reasons.append(
            f"Tagesgrenze erreicht: {sent_today} von {limits.max_sends_per_day} "
            "erlaubten Versendungen heute."
        )
    if organization and last_sent_to_organization is not None:
        minimum = timedelta(days=limits.days_between_same_organization)
        gap = now - last_sent_to_organization
        if gap < minimum:
            reasons.append(
                f"An '{organization}' zuletzt vor {gap.days} Tag(en) geschrieben, "
                f"Mindestabstand sind {limits.days_between_same_organization} Tage."
            )
    return reasons


def enforce(
    limits: SendLimits,
    *,
    score: int | None,
    organization: str | None,
    sent_today: int,
    last_sent_to_organization: datetime | None,
    now: datetime,
) -> None:
    """Raise :class:`LimitError` if any limit is violated. No parameter turns this off."""
    violations = limit_violations(
        limits,
        score=score,
        organization=organization,
        sent_today=sent_today,
        last_sent_to_organization=last_sent_to_organization,
        now=now,
    )
    if violations:
        raise LimitError("; ".join(violations))


@runtime_checkable
class SendLog(Protocol):
    """Where approval counts for the anti-mass rule are read from and written to.

    Deliberately not :class:`anlass.interfaces.Store` itself: the gate asks two
    questions ("how many today", "when did this organisation last hear from us") that
    no single store method answers, and the seam keeps that translation out of both.
    :class:`StoreSendLog` is the implementation that answers them from persisted
    approvals; :class:`InMemorySendLog` is the one that forgets on restart.
    """

    def record(self, organization: str | None, when: datetime) -> None:
        """Record one approval (the closest this package gets to 'about to be sent')."""

    def count_since(self, start: datetime) -> int:
        """How many records exist at or after ``start``."""

    def last_sent(self, organization: str) -> datetime | None:
        """The most recent record for ``organization``, or ``None``."""


class InMemorySendLog:
    """``SendLog`` that is correct within one process and gone on restart.

    Enough for a test or a throwaway run. **Not enough for real use**: a restart sets
    the daily count back to zero, which removes the daily cap in practice. Anything
    that keeps a database uses :class:`StoreSendLog` instead.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[str | None, datetime]] = []

    def record(self, organization: str | None, when: datetime) -> None:
        self._entries.append((organization, when))

    def count_since(self, start: datetime) -> int:
        return sum(1 for _, when in self._entries if when >= start)

    def last_sent(self, organization: str) -> datetime | None:
        matches = [when for org, when in self._entries if org == organization]
        return max(matches) if matches else None


class StoreSendLog:
    """``SendLog`` that answers from persisted approvals, so a restart changes nothing.

    Counted are transitions into ``APPROVED``, one per released draft: a draft that is
    approved and then sent leaves two records, and counting both would halve the daily
    cap without anybody noticing.

    Args:
        store: Any :class:`anlass.interfaces.Store`. Only the protocol is used, so a
            Postgres or Supabase adapter works here unchanged.
        lookback_days: How far back :meth:`last_sent` looks. Anything older than this
            cannot block a contact anyway, so the window bounds the work instead of
            reading the whole history; keep it at or above
            ``SendLimits.days_between_same_organization``.
    """

    def __init__(self, store: Store, *, lookback_days: int = 365) -> None:
        self._store = store
        self._lookback = timedelta(days=max(1, int(lookback_days)))

    def record(self, organization: str | None, when: datetime) -> None:
        """Nothing to do: the approval this stands for is already in the store.

        :func:`anlass.gate.approve.transition` writes the record before the gate calls
        this, so writing anything here would count the same release twice.
        """

    def count_since(self, start: datetime) -> int:
        return len(self._store.approvals_since(start, to_state=ApprovalState.APPROVED))

    def last_sent(self, organization: str) -> datetime | None:
        """When ``organization`` was last written to, within the lookback window.

        Resolves approval to draft to lead per record, because the organisation lives
        on the lead and no store method joins across the three. The daily cap keeps the
        number of records small, and the window keeps it bounded; a store that ever
        holds enough approvals for this to hurt should answer the join itself.
        """
        seen: dict[str, str | None] = {}
        for record in self._store.approvals_since(
            utcnow() - self._lookback, to_state=ApprovalState.APPROVED
        ):
            if record.draft_id not in seen:
                draft = self._store.get_draft(record.draft_id)
                lead = None if draft is None else self._store.get_lead(draft.lead_id)
                seen[record.draft_id] = None if lead is None else lead.value(Field.ORGANIZATION)
            if seen[record.draft_id] == organization:
                return record.decided_at  # newest first, so the first hit is the latest
        return None
