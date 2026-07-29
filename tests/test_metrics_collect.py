"""What became of every draft, assembled out of a real store.

This is the half of the metrics that reads the database, so these tests build one -
a throwaway SQLite store, no network, no model. The arithmetic on top of the assembled
stream is tested in ``test_metrics_report.py`` without any store at all.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from anlass.metrics.collect import collect_outcomes, same_text
from anlass.models import (
    CriterionOutcome,
    Delivery,
    Draft,
    FieldValue,
    Lead,
    OutboundMessage,
    Paragraph,
    Reply,
    ReplyKind,
    ScoreResult,
    Signal,
    TransportReceipt,
    utcnow,
)
from anlass.store.sqlite import SqliteStore


@pytest.fixture
def store():
    with SqliteStore(":memory:") as instance:
        instance.migrate()
        yield instance


def a_lead(store, *, source: str = "rss") -> Lead:
    lead = Lead(source=source, text="Wir bauen unsere Datenverarbeitung selbst.")
    lead.set("organization", FieldValue("Halbinsel Datentechnik GmbH", provider=source))
    store.save_lead(lead)
    return lead


def a_draft(store, lead: Lead, *, text: str = "Wie erzeugt.", signal_id: str | None = None,
            model: str = "ollama:qwen3:8b") -> Draft:
    draft = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text=text)],
        signal_id=signal_id,
        subject="Ihre Ausschreibung",
        model=model,
    )
    store.save_draft(draft)
    return draft


def delivered(store, draft: Draft, *, body: str | None = None, accepted: bool = True) -> None:
    store.save_delivery(
        Delivery(
            message=OutboundMessage(
                draft_id=draft.id,
                recipient="post@beispiel.example",
                subject=draft.subject or "",
                body=draft.text if body is None else body,
            ),
            receipt=TransportReceipt(transport="file", accepted=accepted, reference="ref"),
        )
    )


def answered(store, draft: Draft, kind: ReplyKind, *, message_id: str = "m1", ago_days: int = 0) -> None:
    store.save_reply(
        Reply(
            message_id=message_id,
            sender="post@beispiel.example",
            subject="Re: Ihre Ausschreibung",
            body="Text",
            draft_id=draft.id,
            kind=kind,
            received_at=utcnow() - timedelta(days=ago_days),
        )
    )


# ------------------------------------------------------------------ the plain shape


def test_a_draft_without_history_is_neither_sent_nor_answered_nor_edited(store):
    lead = a_lead(store)
    draft = a_draft(store, lead)

    outcome = collect_outcomes(store)[0]
    assert outcome.draft_id == draft.id
    assert outcome.source == "rss"
    assert outcome.sent is False
    assert outcome.reply_kind is None
    assert outcome.edited_by_hand is False
    assert outcome.variant == "ollama:qwen3:8b"


def test_score_and_occasion_come_from_the_stored_run(store):
    lead = a_lead(store)
    store.save_score(
        ScoreResult(
            lead_id=lead.id,
            total=11,
            outcomes=(CriterionOutcome(name="baut_selbst", weight=3, passed=True, reason="ok"),),
        )
    )
    signal = Signal(lead_id=lead.id, kind="eigenbau", quote="Wir bauen selbst.", detector="test")
    store.save_signal(signal)
    a_draft(store, lead, signal_id=signal.id)

    outcome = collect_outcomes(store)[0]
    assert outcome.score == 11
    assert outcome.signal_kind == "eigenbau"


def test_an_unscored_lead_is_unscored_and_not_a_zero(store):
    """Zero would put the draft in the worst band and make that band look worse."""
    lead = a_lead(store)
    a_draft(store, lead)

    assert collect_outcomes(store)[0].score is None


def test_a_lead_that_is_gone_does_not_take_the_report_down(store):
    draft = Draft(lead_id="lead_geloescht", paragraphs=[Paragraph(text="Text")])
    store.save_draft(draft)

    assert collect_outcomes(store)[0].source == "unbekannt"


# ----------------------------------------------------------------- sent and answered


def test_only_an_accepted_delivery_counts_as_sent(store):
    lead = a_lead(store)
    refused = a_draft(store, lead)
    delivered(store, refused, accepted=False)

    assert collect_outcomes(store)[0].sent is False


def test_a_real_answer_beats_an_earlier_robot(store):
    """An out-of-office followed by a real answer is a real answer."""
    lead = a_lead(store)
    draft = a_draft(store, lead)
    delivered(store, draft)
    answered(store, draft, ReplyKind.AUTO_REPLY, message_id="m1", ago_days=2)
    answered(store, draft, ReplyKind.INTERESTED, message_id="m2", ago_days=1)

    assert collect_outcomes(store)[0].reply_kind is ReplyKind.INTERESTED


def test_a_robot_alone_is_reported_as_a_robot(store):
    lead = a_lead(store)
    draft = a_draft(store, lead)
    delivered(store, draft)
    answered(store, draft, ReplyKind.AUTO_REPLY)

    assert collect_outcomes(store)[0].reply_kind is ReplyKind.AUTO_REPLY


# -------------------------------------------------------------- the honest metric


def test_a_draft_sent_as_written_did_not_need_a_hand(store):
    lead = a_lead(store)
    draft = a_draft(store, lead)
    delivered(store, draft)

    assert collect_outcomes(store)[0].edited_by_hand is False


def test_a_text_changed_before_sending_is_the_honest_number(store):
    lead = a_lead(store)
    draft = a_draft(store, lead, text="Wie erzeugt.")
    delivered(store, draft, body="Von Hand umgeschrieben.")

    assert collect_outcomes(store)[0].edited_by_hand is True


def test_a_draft_corrected_but_not_yet_sent_also_counts(store):
    lead = a_lead(store)
    draft = a_draft(store, lead, text="Wie erzeugt.")
    draft.paragraphs = [Paragraph(text="Von Hand nachgebessert.")]
    store.save_draft(draft)

    assert collect_outcomes(store)[0].edited_by_hand is True


def test_rewrapping_is_formatting_and_not_a_correction(store):
    lead = a_lead(store)
    draft = a_draft(store, lead, text="Ein Satz mit Umbruch.")
    delivered(store, draft, body="Ein Satz\nmit   Umbruch.")

    assert collect_outcomes(store)[0].edited_by_hand is False
    assert same_text("a  b\nc", "a b c")
    assert not same_text("a b", "a b!")


def test_outcomes_come_back_newest_first_and_can_be_limited(store):
    lead = a_lead(store)
    older = a_draft(store, lead, text="alt")
    newer = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text="neu")],
        created_at=utcnow() + timedelta(minutes=1),
    )
    store.save_draft(newer)

    assert [o.draft_id for o in collect_outcomes(store)] == [newer.id, older.id]
    assert [o.draft_id for o in collect_outcomes(store, limit=1)] == [newer.id]
