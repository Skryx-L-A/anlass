"""The store keeps what the later stages need, provenance included."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from anlass.errors import StoreError
from anlass.models import (
    ApprovalRecord,
    ApprovalState,
    CriterionOutcome,
    Delivery,
    Draft,
    Finding,
    FindingKind,
    FollowUp,
    OutboundMessage,
    Paragraph,
    Reply,
    ReplyKind,
    ScoreResult,
    Severity,
    TransportReceipt,
    VerificationResult,
    utcnow,
)
from anlass.store.sqlite import SCHEMA_VERSION, SqliteStore


@pytest.fixture
def store():
    with SqliteStore(":memory:") as instance:
        instance.migrate()
        yield instance


def test_migrating_twice_changes_nothing(store):
    store.migrate()
    row = store._connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] == SCHEMA_VERSION


def test_a_database_from_the_future_is_refused(tmp_path):
    path = tmp_path / "anlass.db"
    with SqliteStore(path) as instance:
        instance.migrate()
        instance._connection.execute("INSERT INTO schema_version (version) VALUES (?)", (99,))
        instance._connection.commit()
    with SqliteStore(path) as instance:
        with pytest.raises(StoreError):
            instance.migrate()


def test_lead_keeps_every_field_with_its_provenance(store, lead):
    store.save_lead(lead)
    loaded = store.get_lead(lead.id)
    assert loaded is not None
    assert loaded.source_ref == lead.source_ref
    assert loaded.text == lead.text
    assert loaded.value("organization") == "Nordlicht Systeme"
    assert loaded.value("remote") is True
    assert loaded.provider_of("remote") == "extraktion"
    assert loaded.fields["remote"].confidence == pytest.approx(0.7)
    assert loaded.fields["remote"].retrieved_at == lead.fields["remote"].retrieved_at


def test_saving_a_lead_twice_does_not_duplicate_fields(store, lead):
    store.save_lead(lead)
    lead.set("contact_email", type(lead.fields["remote"])("a@b.example", provider="raten", confidence=0.3))
    store.save_lead(lead)
    assert len(store.list_leads()) == 1
    assert store.get_lead(lead.id).value("contact_email") == "a@b.example"


def test_unknown_ids_return_none(store):
    assert store.get_lead("gibt-es-nicht") is None
    assert store.get_draft("gibt-es-nicht") is None
    assert store.latest_verification("gibt-es-nicht") is None


def test_signals_are_kept_with_their_offsets(store, lead, signal):
    store.save_lead(lead)
    store.save_signal(signal)
    loaded = store.signals_for_lead(lead.id)
    assert len(loaded) == 1
    assert loaded[0].quote == signal.quote
    assert (loaded[0].start, loaded[0].end) == (signal.start, signal.end)
    assert loaded[0].detector == "stichwort"


def test_a_signal_can_be_looked_up_by_its_own_id(store, lead, signal):
    """Q13: a draft names the occasion it answered, and that one has to be findable.

    Without this the only way in is by lead, and a lead collects one more occasion with
    every run - so the lookup returns the oldest, which is a different sentence than the
    one the draft was written against.
    """
    store.save_lead(lead)
    store.save_signal(signal)

    assert store.signal_by_id(signal.id).quote == signal.quote
    assert store.signal_by_id("gibt-es-nicht") is None


def test_draft_keeps_the_citations_per_paragraph(store, lead, signal):
    draft = Draft(
        lead_id=lead.id,
        signal_id=signal.id,
        subject="Ihre Ausschreibung",
        model="fake:test",
        paragraphs=[
            Paragraph("Anrede.", ()),
            Paragraph("Zahlen.", ("pipeline-latenz", "tests-abdeckung")),
            Paragraph("Gruss.", ()),
        ],
    )
    store.save_draft(draft)
    loaded = store.get_draft(draft.id)
    assert [p.text for p in loaded.paragraphs] == ["Anrede.", "Zahlen.", "Gruss."]
    assert loaded.paragraphs[1].fact_ids == ("pipeline-latenz", "tests-abdeckung")
    assert loaded.paragraphs[0].fact_ids == ()
    assert loaded.subject == "Ihre Ausschreibung"


def test_saving_a_draft_twice_does_not_duplicate_paragraphs(store, lead):
    draft = Draft(lead_id=lead.id, paragraphs=[Paragraph("Eins.", ("a",))])
    store.save_draft(draft)
    store.save_draft(draft)
    assert len(store.get_draft(draft.id).paragraphs) == 1


def test_verification_findings_survive_a_round_trip(store, lead):
    draft = Draft(lead_id=lead.id, paragraphs=[Paragraph("Text.", ())])
    store.save_draft(draft)
    result = VerificationResult(
        draft_id=draft.id,
        findings=(
            Finding(FindingKind.UNSUPPORTED_NUMBER, Severity.ERROR, "Zahl ohne Beleg", "0,9", 0, "deterministic"),
            Finding(FindingKind.UNCITED_SUPPORT, Severity.WARNING, "Beleg fehlt", "3,8", 0, "deterministic"),
        ),
        checkers=("deterministic",),
    )
    store.save_verification(result)
    loaded = store.latest_verification(draft.id)
    assert loaded.passed is False
    assert loaded.checkers == ("deterministic",)
    assert [f.kind for f in loaded.findings] == [
        FindingKind.UNSUPPORTED_NUMBER,
        FindingKind.UNCITED_SUPPORT,
    ]
    assert loaded.errors[0].excerpt == "0,9"


def test_approval_history_is_append_only_and_yields_the_current_state(store, lead):
    draft = Draft(lead_id=lead.id, paragraphs=[Paragraph("Text.", ())])
    store.save_draft(draft)
    assert store.current_state(draft.id) is ApprovalState.PENDING
    for record in (
        ApprovalRecord(draft.id, ApprovalState.PENDING, ApprovalState.APPROVED, "mensch", "geprueft"),
        ApprovalRecord(draft.id, ApprovalState.APPROVED, ApprovalState.SENT, "transport", "smtp"),
    ):
        store.save_approval(record)
    history = store.approvals_for_draft(draft.id)
    assert [r.to_state for r in history] == [ApprovalState.APPROVED, ApprovalState.SENT]
    assert history[0].reason == "geprueft"
    assert store.current_state(draft.id) is ApprovalState.SENT


# ------------------------------------------------------- scores (phase 3, gap a)


def test_a_score_survives_a_round_trip_with_every_reason(store, lead):
    store.save_lead(lead)
    result = ScoreResult(
        lead_id=lead.id,
        total=8,
        outcomes=(
            CriterionOutcome(name="baut_selbst", weight=3, passed=True, reason="Text sagt es"),
            CriterionOutcome(name="klein", weight=2, passed=False, reason="keine Angabe"),
        ),
    )
    store.save_score(result)

    loaded = store.latest_score(lead.id)
    assert loaded is not None
    assert loaded.total == 8 and loaded.accepted is True
    assert [(o.name, o.passed, o.weight) for o in loaded.outcomes] == [
        ("baut_selbst", True, 3),
        ("klein", False, 2),
    ]
    assert loaded.outcomes[1].reason == "keine Angabe"


def test_re_scoring_replaces_instead_of_piling_up(store, lead):
    """A lead has one current score. Two would leave the gate guessing which one holds."""
    store.save_lead(lead)
    store.save_score(ScoreResult(lead_id=lead.id, total=3, outcomes=()))
    store.save_score(
        ScoreResult(
            lead_id=lead.id,
            total=9,
            outcomes=(CriterionOutcome(name="neu", weight=9, passed=True, reason="jetzt schon"),),
        )
    )

    loaded = store.latest_score(lead.id)
    assert loaded.total == 9
    assert len(loaded.outcomes) == 1


def test_an_exclusion_is_kept_as_such(store, lead):
    store.save_lead(lead)
    store.save_score(
        ScoreResult(lead_id=lead.id, total=2, outcomes=(), excluded_by="score < 6")
    )
    loaded = store.latest_score(lead.id)
    assert loaded.accepted is False and loaded.excluded_by == "score < 6"


def test_an_unscored_lead_yields_none_and_does_not_raise(store, lead):
    store.save_lead(lead)
    assert store.latest_score(lead.id) is None
    assert store.latest_score("lead_gibtsnicht") is None


# ------------------------------------------ approvals across drafts (phase 3, gap b)


def _approved(draft_id: str, when: datetime) -> ApprovalRecord:
    return ApprovalRecord(
        draft_id=draft_id,
        from_state=ApprovalState.PENDING,
        to_state=ApprovalState.APPROVED,
        actor="mensch",
        decided_at=when,
    )


def test_approvals_since_spans_drafts_and_comes_back_newest_first(store):
    now = utcnow()
    for index, age in enumerate((3, 1, 2)):
        store.save_approval(_approved(f"draft_{index}", now - timedelta(hours=age)))

    found = store.approvals_since(now - timedelta(days=1))
    assert [record.draft_id for record in found] == ["draft_1", "draft_2", "draft_0"]


def test_approvals_since_ignores_what_is_older_than_the_start(store):
    now = utcnow()
    store.save_approval(_approved("draft_alt", now - timedelta(days=2)))
    store.save_approval(_approved("draft_neu", now - timedelta(minutes=5)))

    found = store.approvals_since(now - timedelta(days=1))
    assert [record.draft_id for record in found] == ["draft_neu"]


def test_approvals_since_can_count_releases_without_counting_the_send(store):
    """A released and then sent draft leaves two records but is one release."""
    now = utcnow()
    store.save_approval(_approved("draft_1", now))
    store.save_approval(
        ApprovalRecord(
            draft_id="draft_1",
            from_state=ApprovalState.APPROVED,
            to_state=ApprovalState.SENT,
            actor="transport",
            decided_at=now,
        )
    )

    assert len(store.approvals_since(now - timedelta(days=1))) == 2
    assert len(store.approvals_since(now - timedelta(days=1), to_state=ApprovalState.APPROVED)) == 1


def test_approvals_since_refuses_a_timestamp_without_a_timezone(store):
    """Comparison happens on ISO strings; a naive value would sort wrong, silently."""
    with pytest.raises(StoreError):
        store.approvals_since(datetime(2026, 7, 29, 5, 0, 0))


# ---------------------------------------- what left, what came back (phase 4, BEFUND 5)


def _delivery(draft_id: str, *, body: str = "Text", when: datetime | None = None,
              accepted: bool = True, reference: str = "<abc@beispiel.example>") -> Delivery:
    return Delivery(
        message=OutboundMessage(
            draft_id=draft_id, recipient="post@beispiel.example", subject="Betreff", body=body
        ),
        receipt=TransportReceipt(
            transport="file",
            accepted=accepted,
            reference=reference,
            sent_at=when or utcnow(),
        ),
    )


def test_a_delivery_keeps_the_text_that_actually_left(store):
    """The receipt alone cannot answer the honest metric - the message can."""
    store.save_delivery(_delivery("draft_1", body="Was rausging."))

    stored = store.list_deliveries(draft_id="draft_1")
    assert len(stored) == 1
    assert stored[0].message.body == "Was rausging."
    assert stored[0].receipt.reference == "<abc@beispiel.example>"
    assert stored[0].draft_id == "draft_1"


def test_deliveries_come_back_newest_first_and_can_be_filtered(store):
    now = utcnow()
    store.save_delivery(_delivery("draft_alt", when=now - timedelta(days=2)))
    store.save_delivery(_delivery("draft_neu", when=now))

    assert [d.draft_id for d in store.list_deliveries()] == ["draft_neu", "draft_alt"]
    assert [d.draft_id for d in store.list_deliveries(draft_id="draft_alt")] == ["draft_alt"]


def test_the_generated_version_survives_a_correction(store):
    """The yardstick of the honest metric must not move when the draft does."""
    draft = Draft(lead_id="lead_1", paragraphs=[Paragraph(text="Wie erzeugt.")])
    store.save_draft(draft)
    draft.paragraphs = [Paragraph(text="Von Hand nachgebessert.")]
    store.save_draft(draft)

    assert store.generated_text(draft.id) == "Wie erzeugt."
    assert store.get_draft(draft.id).text == "Von Hand nachgebessert."


def test_an_unknown_draft_has_no_generated_version(store):
    assert store.generated_text("draft_gibtsnicht") is None


def test_drafts_are_listed_newest_first(store):
    now = utcnow()
    older = Draft(lead_id="lead_1", paragraphs=[Paragraph(text="alt")], created_at=now - timedelta(hours=1))
    newer = Draft(lead_id="lead_1", paragraphs=[Paragraph(text="neu")], created_at=now)
    store.save_draft(older)
    store.save_draft(newer)

    assert [d.id for d in store.list_drafts()] == [newer.id, older.id]
    assert [d.id for d in store.list_drafts(limit=1)] == [newer.id]


def _reply(message_id: str, *, draft_id: str | None = "draft_1", kind: ReplyKind = ReplyKind.INTERESTED,
           when: datetime | None = None) -> Reply:
    return Reply(
        message_id=message_id,
        sender="post@beispiel.example",
        subject="Re: Betreff",
        body="Gern reden.",
        draft_id=draft_id,
        kind=kind,
        received_at=when or utcnow(),
    )


def test_the_same_reply_polled_twice_stays_one_reply(store):
    """Polling is repeatable; a second row would count as a second answer."""
    store.save_reply(_reply("<mail-1@beispiel.example>", kind=ReplyKind.UNKNOWN))
    store.save_reply(_reply("<mail-1@beispiel.example>", kind=ReplyKind.INTERESTED))

    stored = store.list_replies()
    assert len(stored) == 1
    assert stored[0].kind is ReplyKind.INTERESTED


def test_replies_can_be_read_per_draft_and_uncorrelated_ones_stay_out(store):
    store.save_reply(_reply("<a@beispiel.example>", draft_id="draft_1"))
    store.save_reply(_reply("<b@beispiel.example>", draft_id=None))

    assert [r.message_id for r in store.list_replies(draft_id="draft_1")] == ["<a@beispiel.example>"]
    assert len(store.list_replies()) == 2


def test_one_open_follow_up_per_draft(store):
    now = utcnow()
    store.save_follow_up(FollowUp(draft_id="draft_1", due_at=now + timedelta(days=7)))
    store.save_follow_up(FollowUp(draft_id="draft_1", due_at=now + timedelta(days=14)))

    entries = store.list_follow_ups()
    assert len(entries) == 1
    assert entries[0].due_at == now + timedelta(days=14)


def test_follow_ups_can_be_asked_for_what_is_due(store):
    now = utcnow()
    store.save_follow_up(FollowUp(draft_id="draft_faellig", due_at=now - timedelta(days=1)))
    store.save_follow_up(FollowUp(draft_id="draft_spaeter", due_at=now + timedelta(days=1)))

    assert [e.draft_id for e in store.list_follow_ups(due_by=now)] == ["draft_faellig"]
    assert len(store.list_follow_ups()) == 2


def test_a_due_date_without_a_timezone_is_refused(store):
    """Same reason as ``approvals_since``: ISO strings only sort right in UTC."""
    with pytest.raises(StoreError):
        store.list_follow_ups(due_by=datetime(2026, 7, 29, 5, 0, 0))


def test_a_file_store_creates_its_directory(tmp_path):
    path = tmp_path / "unterordner" / "anlass.db"
    with SqliteStore(path) as instance:
        instance.migrate()
    assert path.is_file()
