"""Response rate by source, score band, occasion type, draft variant - and the honest
number: how many drafts had to be fixed by hand.

The plan (section 8) is explicit that this is the number that matters, not how many
drafts were generated - and that it gets published even when it is unflattering.

Why this module still does not read the ``Store``
-------------------------------------------------

It could now: the store persists deliveries, replies and the generated version of every
draft since schema 3. The split is kept anyway, because aggregating and querying are
different jobs with different failure modes - reading rows out of a database is
:mod:`anlass.metrics.collect`, and everything here is arithmetic over a finished stream
of :class:`DraftOutcome`. That is what lets a test state a situation in six lines
instead of building a database, and what lets a different store (Postgres, Supabase)
supply the same stream without touching a single rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from ..models import ReplyKind

__all__ = [
    "DEFAULT_SCORE_BANDS",
    "DraftOutcome",
    "RateStat",
    "manual_edit_rate",
    "render_report",
    "response_rate_by",
    "score_band",
]


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """What became of one draft - the unit every report here aggregates.

    Args:
        draft_id: Identifies the draft this outcome describes.
        source: ``Lead.source`` of the recipient this draft was written for.
        score: ``ScoreResult.total`` at the time of scoring, or ``None`` if the lead was
            never scored. ``None`` gets its own band rather than counting as zero: an
            unscored lead is not a badly scored one, and a rate that mixes the two says
            the low band performs worse than it does.
        signal_kind: ``Signal.kind``, or ``None`` if the draft had no signal.
        variant: ``Draft.model`` - the generation variant, e.g. which prompt or model
            produced it, so A/B differences show up here even before the plan's
            statistical comparison (section 7, roadmap) exists.
        sent: Whether the draft was actually sent. Unsent drafts are excluded from
            every rate below a denominator of "sent drafts" would be misleading for.
        reply_kind: The classified reply, if one came back. ``None`` means no reply
            yet, not "no reply ever" - the caller decides how long to wait before
            calling that final.
        edited_by_hand: Whether the text that was actually sent differs from what the
            drafter produced. This is the honest number from the plan.
    """

    draft_id: str
    source: str
    score: int | None
    signal_kind: str | None
    variant: str
    sent: bool
    reply_kind: ReplyKind | None = None
    edited_by_hand: bool = False


#: (exclusive upper bound, label), checked in order. The last entry's bound is never
#: reached in practice; it only needs to be higher than any real score.
DEFAULT_SCORE_BANDS: tuple[tuple[int, str], ...] = (
    (6, "unter 6"),
    (10, "6-9"),
    (15, "10-14"),
    (1_000_000, "15+"),
)


def score_band(
    score: int | None, bands: Sequence[tuple[int, str]] = DEFAULT_SCORE_BANDS
) -> str:
    """Which band ``score`` falls into, by the table above. ``None`` is its own band."""
    if score is None:
        return "unbewertet"
    for ceiling, label in bands:
        if score < ceiling:
            return label
    return bands[-1][1]


@dataclass(frozen=True, slots=True)
class RateStat:
    """A count and how many of them counted as positive."""

    total: int
    positive: int

    @property
    def rate(self) -> float:
        return self.positive / self.total if self.total else 0.0


def _counted_as_reply(outcome: DraftOutcome) -> bool:
    """A real answer, not silence and not a mailbox robot answering for someone."""
    return outcome.reply_kind is not None and outcome.reply_kind is not ReplyKind.AUTO_REPLY


def response_rate_by(outcomes: Iterable[DraftOutcome], key: Callable[[DraftOutcome], str]) -> dict[str, RateStat]:
    """Response rate grouped by ``key``, over sent drafts only."""
    totals: dict[str, int] = {}
    positives: dict[str, int] = {}
    for outcome in outcomes:
        if not outcome.sent:
            continue
        group = key(outcome)
        totals[group] = totals.get(group, 0) + 1
        if _counted_as_reply(outcome):
            positives[group] = positives.get(group, 0) + 1
    return {group: RateStat(total=total, positive=positives.get(group, 0)) for group, total in totals.items()}


def manual_edit_rate(outcomes: Iterable[DraftOutcome]) -> RateStat:
    """The honest metric: how many drafts needed a human fix, of every draft made."""
    items = list(outcomes)
    edited = sum(1 for outcome in items if outcome.edited_by_hand)
    return RateStat(total=len(items), positive=edited)


def _render_section(title: str, stats: dict[str, RateStat]) -> str:
    lines = [title, "-" * len(title)]
    if not stats:
        lines.append("Keine versendeten Entwuerfe.")
    else:
        for group in sorted(stats):
            stat = stats[group]
            lines.append(f"  {group}: {stat.positive}/{stat.total} ({stat.rate:.0%})")
    return "\n".join(lines)


def render_report(outcomes: Sequence[DraftOutcome]) -> str:
    """The full terminal report, German, in the order the plan lists the breakdowns."""
    sections = [
        _render_section("Antwortquote nach Quelle", response_rate_by(outcomes, lambda o: o.source)),
        _render_section(
            "Antwortquote nach Punktband", response_rate_by(outcomes, lambda o: score_band(o.score))
        ),
        _render_section(
            "Antwortquote nach Anlasstyp",
            response_rate_by(outcomes, lambda o: o.signal_kind or "ohne Anlass"),
        ),
        _render_section(
            "Antwortquote nach Entwurfsvariante",
            response_rate_by(outcomes, lambda o: o.variant or "unbekannt"),
        ),
    ]
    edit_stat = manual_edit_rate(outcomes)
    edit_title = "Ehrliche Kennzahl: von Hand nachgebesserte Entwuerfe"
    if edit_stat.total:
        edit_line = (
            f"  {edit_stat.positive}/{edit_stat.total} Entwuerfe wurden von Hand "
            f"nachgebessert ({edit_stat.rate:.0%})."
        )
    else:
        edit_line = "  Keine Entwuerfe vorhanden."
    sections.append("\n".join([edit_title, "-" * len(edit_title), edit_line]))

    header = "Kennzahlen"
    return "\n\n".join([header, "=" * len(header), *sections])
