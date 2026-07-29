"""Stage 8, RuleGate: the anlass.interfaces.Gate implementation, wired to the limits.

Phase 3 changed one thing here on purpose, and these tests changed with it: the gate no
longer takes a score from its caller, it reads the lead's score out of the store. Every
test that used to pass ``score=`` now saves the score first - which is also what the
pipeline does at stage 4. A gate that believed the number in the call could be handed a
high one for a low lead, and the minimum-score rule would have been a courtesy.
"""

from __future__ import annotations

import pytest

from anlass.gate.approve import ApprovalError
from anlass.gate.gate import RuleGate
from anlass.gate.limits import InMemorySendLog, SendLimits
from anlass.models import (
    ApprovalState,
    Draft,
    FieldValue,
    Finding,
    FindingKind,
    Lead,
    Paragraph,
    ScoreResult,
    Severity,
    VerificationResult,
)
from anlass.store.sqlite import SqliteStore

LIMITS = SendLimits(max_sends_per_day=2, days_between_same_organization=30, min_score=6)


@pytest.fixture
def store() -> SqliteStore:
    store = SqliteStore()
    store.migrate()
    return store


def _lead_with_org(store: SqliteStore, organization: str, *, score: int | None = 9) -> Lead:
    lead = Lead(source="datei", text="Wir bauen selbst.")
    lead.set("organization", FieldValue(organization, provider="datei"))
    store.save_lead(lead)
    if score is not None:
        store.save_score(ScoreResult(lead_id=lead.id, total=score, outcomes=()))
    return lead


def _draft(store: SqliteStore, lead: Lead, *, with_signal: bool = True) -> Draft:
    draft = Draft(
        lead_id=lead.id,
        signal_id="sig_1" if with_signal else None,
        paragraphs=[Paragraph(text="Hallo")],
    )
    store.save_draft(draft)
    return draft


def _pass(draft_id: str) -> VerificationResult:
    return VerificationResult(draft_id=draft_id, findings=())


def _fail(draft_id: str) -> VerificationResult:
    return VerificationResult(
        draft_id=draft_id,
        findings=(Finding(kind=FindingKind.UNSUPPORTED_NUMBER, severity=Severity.ERROR, message="x"),),
    )


def test_conforms_to_the_gate_protocol(store: SqliteStore):
    """Both import paths: the contract lives in interfaces, pipeline re-exports it."""
    from anlass.interfaces import Gate
    from anlass.pipeline import Gate as ReExported

    gate = RuleGate(store, LIMITS)
    assert isinstance(gate, Gate)
    assert ReExported is Gate


def test_check_is_clean_for_a_well_formed_draft(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)
    assert gate.check(draft) == []


def test_the_gate_reads_the_score_itself_and_a_caller_cannot_talk_it_up(store: SqliteStore):
    """The point of the change: a passed-in score cannot beat the stored one."""
    lead = _lead_with_org(store, "Nordlicht", score=2)
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)

    flattering = ScoreResult(lead_id=lead.id, total=99, outcomes=())
    objections = gate.check(draft, score=flattering)

    assert any("Mindestpunktzahl" in o for o in objections)
    with pytest.raises(ApprovalError):
        gate.approve(draft, actor="mara")


def test_an_unscored_lead_is_blocked_even_with_a_passed_in_score(store: SqliteStore):
    """No stored score means not cleared. A caller may fill the gap, not create one."""
    lead = _lead_with_org(store, "Nordlicht", score=None)
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)

    assert any("unbewertet" in o for o in gate.check(draft))
    assert gate.check(draft, score=ScoreResult(lead_id=lead.id, total=9, outcomes=())) == []


def test_check_objects_when_no_signal_and_when_verification_failed(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    draft_no_signal = _draft(store, lead, with_signal=False)
    store.save_verification(_pass(draft_no_signal.id))
    gate = RuleGate(store, LIMITS)
    objections = gate.check(draft_no_signal)
    assert any("Anlass" in o for o in objections)

    draft_failed = _draft(store, lead)
    store.save_verification(_fail(draft_failed.id))
    objections2 = gate.check(draft_failed)
    assert any("Pruefung" in o for o in objections2)


def test_approve_persists_the_transition_and_records_the_send_log(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    log = InMemorySendLog()
    gate = RuleGate(store, LIMITS, send_log=log)

    record = gate.approve(draft, actor="mara")

    assert record.to_state is ApprovalState.APPROVED
    assert store.current_state(draft.id) is ApprovalState.APPROVED
    assert log.last_sent("Nordlicht") == record.decided_at


def test_approve_under_the_minimum_score_is_refused(store: SqliteStore):
    """Mandatory: a lead under the minimum score cannot be approved, full pipeline path."""
    lead = _lead_with_org(store, "Nordlicht", score=2)
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)

    with pytest.raises(ApprovalError):
        gate.approve(draft, actor="mara")
    assert store.current_state(draft.id) is ApprovalState.PENDING


def test_daily_cap_blocks_approval_once_reached(store: SqliteStore):
    """Mandatory: the daily send limit actually stops approval, full pipeline path."""
    gate = RuleGate(store, LIMITS)  # max_sends_per_day = 2
    # A distinct organization per draft isolates this from the cooldown rule - each
    # organization is contacted only once, so only the daily cap can be the objection.
    for index in range(LIMITS.max_sends_per_day):
        lead = _lead_with_org(store, f"Organisation {index}")
        draft = _draft(store, lead)
        store.save_verification(_pass(draft.id))
        gate.approve(draft, actor="mara")

    lead = _lead_with_org(store, "Organisation Ueberzaehlig")
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    with pytest.raises(ApprovalError) as excinfo:
        gate.approve(draft, actor="mara")
    assert "Tagesgrenze" in str(excinfo.value)
    assert store.current_state(draft.id) is ApprovalState.PENDING


def test_the_daily_cap_survives_a_new_gate_on_the_same_store(store: SqliteStore):
    """The other half of the fix: the count comes out of the store, not out of memory.

    A fresh ``RuleGate`` stands in for a restarted process - same database, new object,
    no in-memory history. Before phase 3 this test would have passed the third approval
    through, because the count lived in the gate.
    """
    for index in range(LIMITS.max_sends_per_day):
        lead = _lead_with_org(store, f"Organisation {index}")
        draft = _draft(store, lead)
        store.save_verification(_pass(draft.id))
        RuleGate(store, LIMITS).approve(draft, actor="mara")

    lead = _lead_with_org(store, "Organisation Nach Neustart")
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    with pytest.raises(ApprovalError) as excinfo:
        RuleGate(store, LIMITS).approve(draft, actor="mara")
    assert "Tagesgrenze" in str(excinfo.value)


def test_organization_cooldown_blocks_a_second_organization_contact(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    gate = RuleGate(store, LIMITS, send_log=InMemorySendLog())

    first = _draft(store, lead)
    store.save_verification(_pass(first.id))
    gate.approve(first, actor="mara")

    second = _draft(store, lead)
    store.save_verification(_pass(second.id))
    with pytest.raises(ApprovalError) as excinfo:
        gate.approve(second, actor="mara")
    assert "Nordlicht" in str(excinfo.value)


def test_the_organization_cooldown_survives_a_new_gate_on_the_same_store(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    first = _draft(store, lead)
    store.save_verification(_pass(first.id))
    RuleGate(store, LIMITS).approve(first, actor="mara")

    second = _draft(store, lead)
    store.save_verification(_pass(second.id))
    with pytest.raises(ApprovalError) as excinfo:
        RuleGate(store, LIMITS).approve(second, actor="mara")
    assert "Nordlicht" in str(excinfo.value)


def test_approve_has_no_bypass_parameter(store: SqliteStore):
    lead = _lead_with_org(store, "Nordlicht")
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)
    with pytest.raises(TypeError):
        gate.approve(draft, actor="mara", force=True)  # type: ignore[call-arg]


def test_approve_takes_no_score_at_all(store: SqliteStore):
    """A score on ``approve`` would be a bypass with extra steps. It is gone."""
    lead = _lead_with_org(store, "Nordlicht", score=2)
    draft = _draft(store, lead)
    store.save_verification(_pass(draft.id))
    gate = RuleGate(store, LIMITS)
    with pytest.raises(TypeError):
        gate.approve(  # type: ignore[call-arg]
            draft, actor="mara", score=ScoreResult(lead_id=lead.id, total=99, outcomes=())
        )
