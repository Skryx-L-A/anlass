"""Stage 8, the anti-mass rule: no switch turns any of these three checks off."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anlass.errors import AnlassError
from anlass.models import utcnow
from anlass.gate.limits import (
    InMemorySendLog,
    LimitError,
    SendLimits,
    StoreSendLog,
    enforce,
    limit_violations,
)

LIMITS = SendLimits(max_sends_per_day=3, days_between_same_organization=14, min_score=6)
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def test_lead_under_the_minimum_score_cannot_be_approved_even_if_asked_to():
    """Mandatory test: the anti-mass-application rule as an unconditional block."""
    with pytest.raises(LimitError):
        enforce(
            LIMITS,
            score=5,  # one point under the threshold of 6
            organization=None,
            sent_today=0,
            last_sent_to_organization=None,
            now=NOW,
        )
    # There is no parameter that waives the score check - only a higher score does.
    enforce(LIMITS, score=6, organization=None, sent_today=0, last_sent_to_organization=None, now=NOW)


def test_daily_send_cap_blocks_the_fourth_send_of_the_day():
    """Mandatory test: the daily limit actually stops a send once it is reached."""
    for sent_today in range(LIMITS.max_sends_per_day):
        enforce(
            LIMITS,
            score=10,
            organization=None,
            sent_today=sent_today,
            last_sent_to_organization=None,
            now=NOW,
        )
    with pytest.raises(LimitError):
        enforce(
            LIMITS,
            score=10,
            organization=None,
            sent_today=LIMITS.max_sends_per_day,
            last_sent_to_organization=None,
            now=NOW,
        )


def test_organization_cooldown_blocks_a_second_contact_too_soon():
    last_contact = NOW - timedelta(days=1)
    with pytest.raises(LimitError):
        enforce(
            LIMITS,
            score=10,
            organization="Nordlicht Systeme",
            sent_today=0,
            last_sent_to_organization=last_contact,
            now=NOW,
        )


def test_organization_cooldown_allows_contact_once_enough_days_passed():
    last_contact = NOW - timedelta(days=LIMITS.days_between_same_organization + 1)
    enforce(
        LIMITS,
        score=10,
        organization="Nordlicht Systeme",
        sent_today=0,
        last_sent_to_organization=last_contact,
        now=NOW,
    )


def test_score_none_blocks_because_unscored_is_not_cleared():
    # Superseded an earlier test that asserted the opposite. Skipping the check for a
    # caller "with no score to give" was the bypass this module exists to deny: drop
    # stage 4, pass no score, and the minimum evaporates. Unscored is not high-scoring.
    violations = limit_violations(
        LIMITS, score=None, organization=None, sent_today=0, last_sent_to_organization=None, now=NOW
    )
    assert len(violations) == 1
    assert "unbewertet" in violations[0]


def test_violations_are_reported_all_at_once_not_just_the_first():
    violations = limit_violations(
        LIMITS,
        score=1,
        organization="Nordlicht",
        sent_today=LIMITS.max_sends_per_day,
        last_sent_to_organization=NOW - timedelta(days=1),
        now=NOW,
    )
    assert len(violations) == 3


def test_enforce_has_no_bypass_parameter():
    """There is deliberately no --force/--yes-all: passing one is a TypeError, not a no-op."""
    with pytest.raises(TypeError):
        enforce(  # type: ignore[call-arg]
            LIMITS,
            score=1,
            organization=None,
            sent_today=0,
            last_sent_to_organization=None,
            now=NOW,
            force=True,
        )


def test_send_limits_from_criteria_reads_the_shipped_example():
    from anlass.profile import load_profile

    profile = load_profile("profile.example")
    limits = SendLimits.from_criteria(profile.criteria)
    assert limits.max_sends_per_day == 5
    assert limits.days_between_same_organization == 30
    assert limits.min_score == 6


def test_send_limits_from_criteria_requires_all_three_keys():
    with pytest.raises(AnlassError):
        SendLimits.from_criteria({"limits": {"max_sends_per_day": 5}})


def test_in_memory_send_log_counts_and_recalls_last_contact():
    log = InMemorySendLog()
    log.record("Nordlicht", NOW - timedelta(hours=1))
    log.record(None, NOW - timedelta(minutes=1))
    assert log.count_since(NOW - timedelta(days=1)) == 2
    assert log.count_since(NOW + timedelta(seconds=1)) == 0
    assert log.last_sent("Nordlicht") == NOW - timedelta(hours=1)
    assert log.last_sent("Unbekannt") is None


# ---------------------------------------------- the send log that outlives a restart
#
# These use the real clock, not the fixed ``NOW`` above: ``StoreSendLog`` reads
# ``utcnow()`` for its own window, so a fixed constant would make the test pass or fail
# depending on what day it is run.


def _store_with_release(organization: str, *, when=None):
    """A store holding one lead, one draft for it, and one release of that draft."""
    from anlass.models import ApprovalRecord, ApprovalState, Draft, FieldValue, Lead, Paragraph
    from anlass.store.sqlite import SqliteStore

    store = SqliteStore()
    store.migrate()
    lead = Lead(source="datei")
    lead.set("organization", FieldValue(organization, provider="datei"))
    store.save_lead(lead)
    draft = Draft(lead_id=lead.id, paragraphs=[Paragraph("Text")])
    store.save_draft(draft)
    store.save_approval(
        ApprovalRecord(
            draft_id=draft.id,
            from_state=ApprovalState.PENDING,
            to_state=ApprovalState.APPROVED,
            actor="mensch",
            **({"decided_at": when} if when is not None else {}),
        )
    )
    return store, draft


def test_store_send_log_counts_releases_out_of_the_store():
    now = utcnow()
    store, _ = _store_with_release("Nordlicht")
    try:
        log = StoreSendLog(store)
        assert log.count_since(now - timedelta(days=1)) == 1
        assert log.count_since(now + timedelta(days=1)) == 0
    finally:
        store.close()


def test_store_send_log_counts_a_release_once_even_after_the_send():
    """APPROVED and SENT are two records and one release."""
    from anlass.models import ApprovalRecord, ApprovalState

    now = utcnow()
    store, draft = _store_with_release("Nordlicht")
    try:
        store.save_approval(
            ApprovalRecord(
                draft_id=draft.id,
                from_state=ApprovalState.APPROVED,
                to_state=ApprovalState.SENT,
                actor="transport",
            )
        )
        assert StoreSendLog(store).count_since(now - timedelta(days=1)) == 1
    finally:
        store.close()


def test_store_send_log_finds_the_organization_behind_a_release():
    store, _ = _store_with_release("Nordlicht")
    try:
        log = StoreSendLog(store)
        assert log.last_sent("Nordlicht") is not None
        assert log.last_sent("Jemand anderes") is None
    finally:
        store.close()


def test_store_send_log_does_not_look_past_its_window():
    """Anything older than the lookback cannot block a contact anyway."""
    store, _ = _store_with_release("Nordlicht", when=utcnow() - timedelta(days=40))
    try:
        assert StoreSendLog(store, lookback_days=30).last_sent("Nordlicht") is None
        assert StoreSendLog(store, lookback_days=90).last_sent("Nordlicht") is not None
    finally:
        store.close()


def test_store_send_log_record_writes_nothing_twice():
    """The approval is already in the store; recording again would double-count it."""
    now = utcnow()
    store, _ = _store_with_release("Nordlicht")
    try:
        log = StoreSendLog(store)
        log.record("Nordlicht", now)
        assert log.count_since(now - timedelta(days=1)) == 1
    finally:
        store.close()


def test_an_unscored_lead_is_blocked_not_waved_through():
    """score=None must block. Skipping stage 4 must not be a way past the minimum."""
    from datetime import datetime, timezone
    from anlass.gate.limits import SendLimits, limit_violations

    limits = SendLimits.from_criteria(
        {"limits": {"max_sends_per_day": 5, "min_score": 6, "days_between_same_organization": 7}}
    )
    reasons = limit_violations(
        limits,
        score=None,
        organization="Beispiel GmbH",
        sent_today=0,
        last_sent_to_organization=None,
        now=datetime.now(timezone.utc),
    )
    assert reasons, "unbewerteter Lead wurde durchgelassen"
    assert "unbewertet" in reasons[0]
