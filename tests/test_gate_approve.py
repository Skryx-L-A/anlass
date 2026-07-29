"""Stage 8, state machine: approval is a transition with a timestamp and an actor."""

from __future__ import annotations

import pytest

from anlass.gate.approve import ApprovalError, approve, mark_sent, reject, transition
from anlass.models import ApprovalState, VerificationResult
from anlass.store.sqlite import SqliteStore


@pytest.fixture
def store() -> SqliteStore:
    store = SqliteStore()
    store.migrate()
    return store


def _passing_verification(draft_id: str) -> VerificationResult:
    return VerificationResult(draft_id=draft_id, findings=())


def _failing_verification(draft_id: str) -> VerificationResult:
    from anlass.models import Finding, FindingKind, Severity

    return VerificationResult(
        draft_id=draft_id,
        findings=(
            Finding(
                kind=FindingKind.UNSUPPORTED_NUMBER,
                severity=Severity.ERROR,
                message="erfundene Zahl",
            ),
        ),
    )


def test_pending_is_the_state_of_a_draft_with_no_history(store: SqliteStore):
    assert store.current_state("draft_unbekannt") is ApprovalState.PENDING


def test_approve_writes_a_timestamped_record_with_an_actor(store: SqliteStore):
    record = approve(
        store,
        "draft_1",
        actor="mara",
        verification=_passing_verification("draft_1"),
        signal_present=True,
    )
    assert record.to_state is ApprovalState.APPROVED
    assert record.from_state is ApprovalState.PENDING
    assert record.actor == "mara"
    assert record.decided_at is not None
    assert store.current_state("draft_1") is ApprovalState.APPROVED
    history = store.approvals_for_draft("draft_1")
    assert [r.to_state for r in history] == [ApprovalState.APPROVED]


def test_approve_refuses_a_draft_that_failed_verification(store: SqliteStore):
    with pytest.raises(ApprovalError):
        approve(
            store,
            "draft_2",
            actor="mara",
            verification=_failing_verification("draft_2"),
            signal_present=True,
        )
    assert store.current_state("draft_2") is ApprovalState.PENDING


def test_approve_refuses_a_draft_without_an_occasion():
    """Rule 1 - kein Kontakt ohne Anlass - has no exception, not even a clean draft."""
    store = SqliteStore()
    store.migrate()
    with pytest.raises(ApprovalError):
        approve(
            store,
            "draft_3",
            actor="mara",
            verification=_passing_verification("draft_3"),
            signal_present=False,
        )


def test_invalid_transition_is_refused_not_silently_allowed(store: SqliteStore):
    # PENDING -> SENT skips APPROVED entirely.
    with pytest.raises(ApprovalError):
        transition(store, "draft_4", ApprovalState.SENT, actor="mara")


def test_rejected_and_sent_are_terminal(store: SqliteStore):
    reject(store, "draft_5", actor="mara", reason="Kein Anlass mehr aktuell.")
    assert store.current_state("draft_5") is ApprovalState.REJECTED
    with pytest.raises(ApprovalError):
        transition(store, "draft_5", ApprovalState.APPROVED, actor="mara")

    approve(store, "draft_6", actor="mara", verification=_passing_verification("draft_6"), signal_present=True)
    mark_sent(store, "draft_6", actor="mara")
    assert store.current_state("draft_6") is ApprovalState.SENT
    with pytest.raises(ApprovalError):
        mark_sent(store, "draft_6", actor="mara")


def test_reject_requires_a_reason(store: SqliteStore):
    with pytest.raises(ApprovalError):
        reject(store, "draft_7", actor="mara", reason="   ")


def test_transition_requires_a_non_empty_actor(store: SqliteStore):
    with pytest.raises(ApprovalError):
        transition(store, "draft_8", ApprovalState.APPROVED, actor="  ")


def test_mark_sent_only_allowed_from_approved(store: SqliteStore):
    with pytest.raises(ApprovalError):
        mark_sent(store, "draft_9", actor="mara")
