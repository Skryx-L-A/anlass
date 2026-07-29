"""Stage 8, state machine: approval is a state transition, not a checkbox.

Every decision - approve, reject, mark sent - is one
:class:`~anlass.models.ApprovalRecord` with a timestamp and an actor, appended to the
draft's history through :meth:`anlass.interfaces.Store.save_approval`. ``_ALLOWED``
below is the whole rule: which transition is permitted from which state. Anything not
listed is refused with :class:`ApprovalError`, never silently accepted - and
``REJECTED``/``SENT`` are terminal: a rejected draft is reconsidered by generating a
new :class:`~anlass.models.Draft` (stage 6 again), not by resurrecting the old record.

:class:`~anlass.models.ApprovalState` has no separate "reviewed" state; "geprueft" (the
task's third state) is stage 7's :class:`~anlass.models.VerificationResult`, not a
persisted approval state, so it is enforced as a precondition of :func:`approve` rather
than modelled as a fifth enum member that does not exist in the frozen model.
"""

from __future__ import annotations

from ..errors import AnlassError
from ..interfaces import Store
from ..models import ApprovalRecord, ApprovalState, VerificationResult

__all__ = ["ApprovalError", "approve", "mark_sent", "reject", "transition"]


class ApprovalError(AnlassError):
    """An approval transition was refused."""


_ALLOWED: dict[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.PENDING: frozenset({ApprovalState.APPROVED, ApprovalState.REJECTED}),
    ApprovalState.APPROVED: frozenset({ApprovalState.SENT, ApprovalState.REJECTED}),
    ApprovalState.REJECTED: frozenset(),
    ApprovalState.SENT: frozenset(),
}


def transition(
    store: Store,
    draft_id: str,
    to_state: ApprovalState,
    *,
    actor: str,
    reason: str = "",
) -> ApprovalRecord:
    """Append one state transition, refusing anything not in ``_ALLOWED``.

    Raises:
        ApprovalError: The transition is not allowed from the draft's current state,
            or ``actor`` is empty.
    """
    if not actor.strip():
        raise ApprovalError("Eine Freigabe-Aktion braucht einen Urheber.")
    current = store.current_state(draft_id)
    if to_state not in _ALLOWED.get(current, frozenset()):
        raise ApprovalError(
            f"Uebergang von '{current.value}' nach '{to_state.value}' ist nicht erlaubt "
            f"fuer Entwurf '{draft_id}'."
        )
    record = ApprovalRecord(
        draft_id=draft_id, from_state=current, to_state=to_state, actor=actor, reason=reason
    )
    store.save_approval(record)
    return record


def approve(
    store: Store,
    draft_id: str,
    *,
    actor: str,
    verification: VerificationResult,
    signal_present: bool,
    reason: str = "",
) -> ApprovalRecord:
    """Freigabe. Setzt voraus, dass Pruefung und Anlasspflicht schon erfuellt sind.

    ``verification`` steht fuer 'geprueft' (Stufe 7): eine Freigabe ohne bestandene
    Pruefung wird hier abgewiesen, nicht stillschweigend erlaubt. ``signal_present``
    erzwingt Regel 1 aus dem Plan - kein Kontakt ohne Anlass, auch nicht bei einer
    sonst sauberen Pruefung.

    Raises:
        ApprovalError: Verification did not pass, no signal is present, or the
            underlying transition is refused.
    """
    if not verification.passed:
        raise ApprovalError(
            f"Entwurf '{draft_id}' hat die Pruefung nicht bestanden "
            f"({len(verification.errors)} Fehler) und kann nicht freigegeben werden."
        )
    if not signal_present:
        raise ApprovalError(f"Entwurf '{draft_id}' hat keinen Anlass. Regel 1 kennt keine Ausnahme.")
    return transition(store, draft_id, ApprovalState.APPROVED, actor=actor, reason=reason)


def reject(store: Store, draft_id: str, *, actor: str, reason: str) -> ApprovalRecord:
    """Ablehnung. Braucht eine Begruendung - eine Ablehnung ohne Grund ist keine Auskunft."""
    if not reason.strip():
        raise ApprovalError("Eine Ablehnung braucht eine Begruendung.")
    return transition(store, draft_id, ApprovalState.REJECTED, actor=actor, reason=reason)


def mark_sent(store: Store, draft_id: str, *, actor: str, reason: str = "") -> ApprovalRecord:
    """Versand vermerken. Nur aus ``APPROVED`` erlaubt, siehe ``_ALLOWED``."""
    return transition(store, draft_id, ApprovalState.SENT, actor=actor, reason=reason)
