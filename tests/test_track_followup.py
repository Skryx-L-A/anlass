from __future__ import annotations

from datetime import timedelta

from anlass.models import utcnow
from anlass.track.followup import FollowUpBook


def test_schedule_sets_a_due_date_in_the_future() -> None:
    book = FollowUpBook()
    before = utcnow()

    entry = book.schedule("draft_1", after=timedelta(days=5), reason="keine Antwort")

    assert entry.draft_id == "draft_1"
    assert entry.reason == "keine Antwort"
    assert entry.due_at > before


def test_due_returns_only_entries_at_or_before_now() -> None:
    book = FollowUpBook()
    soon = book.schedule("draft_1", after=timedelta(seconds=-1))
    later = book.schedule("draft_2", after=timedelta(days=30))

    due = book.due()

    assert soon in due
    assert later not in due


def test_due_accepts_an_explicit_now() -> None:
    book = FollowUpBook()
    entry = book.schedule("draft_1", after=timedelta(days=10))

    assert book.due(now=utcnow()) == []
    assert entry in book.due(now=entry.due_at + timedelta(seconds=1))


def test_all_lists_every_scheduled_entry_regardless_of_due_date() -> None:
    book = FollowUpBook()
    book.schedule("draft_1", after=timedelta(days=1))
    book.schedule("draft_2", after=timedelta(days=2))

    assert len(book.all()) == 2


def test_the_book_has_no_send_method() -> None:
    """The whole point: scheduling a follow-up cannot, by construction, send one."""
    assert not hasattr(FollowUpBook, "send")
