"""Assemble what became of every draft, out of the store, for :mod:`anlass.metrics.report`.

This is the half that knows the database; the report is the half that only does
arithmetic. Everything here is a read - a report that changed the data it reports on
would be the last thing anybody should trust.

The honest metric of this project is how many drafts had to be corrected by hand, and it
is measured the cheapest honest way there is: a text comparison. Every draft's generated
version is written down at its first save (``Store.generated_text``); what is compared
against it is the text that actually left, if the draft went out, and otherwise the text
the draft has now. Differences in line breaks and spacing do not count - a rewrapped
paragraph is not a correction - but any change to the words does.

What is deliberately *not* counted as an answer: an automatic reply. That rule lives in
:func:`anlass.metrics.report.response_rate_by` and is repeated here when picking which of
several replies to a draft counts, so a mailbox robot cannot make a thread look answered.
"""

from __future__ import annotations

from ..interfaces import Store
from ..models import Reply, ReplyKind
from .report import DraftOutcome

__all__ = ["collect_outcomes", "same_text"]


def _normalized(text: str) -> str:
    """Whitespace-insensitive form. Line wrapping is formatting, not a correction."""
    return " ".join(text.split())


def same_text(left: str, right: str) -> bool:
    """Whether two texts say the same thing, ignoring wrapping and trailing spaces."""
    return _normalized(left) == _normalized(right)


def _counting_reply(replies: list[Reply]) -> Reply | None:
    """The reply that describes this thread: a real one if there is one at all.

    Replies arrive newest first. An auto-reply is only reported when nothing else came
    back, because an out-of-office followed by a real answer is a real answer.
    """
    for reply in replies:
        if reply.kind is not ReplyKind.AUTO_REPLY:
            return reply
    return replies[0] if replies else None


def collect_outcomes(store: Store, *, limit: int | None = None) -> list[DraftOutcome]:
    """One :class:`DraftOutcome` per stored draft, newest first.

    Args:
        store: Read-only here, in every branch.
        limit: Only the newest ``limit`` drafts.
    """
    outcomes: list[DraftOutcome] = []
    for draft in store.list_drafts(limit=limit):
        lead = store.get_lead(draft.lead_id)
        score = store.latest_score(draft.lead_id)

        signal_kind: str | None = None
        if draft.signal_id is not None and lead is not None:
            for signal in store.signals_for_lead(lead.id):
                if signal.id == draft.signal_id:
                    signal_kind = signal.kind
                    break

        deliveries = store.list_deliveries(draft_id=draft.id)
        accepted = [entry for entry in deliveries if entry.receipt.accepted]
        sent_text = accepted[0].message.body if accepted else None

        generated = store.generated_text(draft.id)
        current = sent_text if sent_text is not None else draft.text
        edited = generated is not None and not same_text(generated, current)

        reply = _counting_reply(store.list_replies(draft_id=draft.id))
        outcomes.append(
            DraftOutcome(
                draft_id=draft.id,
                source=lead.source if lead is not None else "unbekannt",
                score=score.total if score is not None else None,
                signal_kind=signal_kind,
                variant=draft.model,
                sent=bool(accepted),
                reply_kind=reply.kind if reply is not None else None,
                edited_by_hand=edited,
            )
        )
    return outcomes
