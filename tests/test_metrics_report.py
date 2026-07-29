from __future__ import annotations

from anlass.metrics.report import DraftOutcome, manual_edit_rate, render_report, response_rate_by, score_band
from anlass.models import ReplyKind


def _outcome(**overrides) -> DraftOutcome:
    fields = dict(
        draft_id="draft_1",
        source="rss",
        score=10,
        signal_kind="eigenbau",
        variant="ollama:qwen3:8b",
        sent=True,
        reply_kind=None,
        edited_by_hand=False,
    )
    fields.update(overrides)
    return DraftOutcome(**fields)


def test_score_band_boundaries() -> None:
    assert score_band(0) == "unter 6"
    assert score_band(5) == "unter 6"
    assert score_band(6) == "6-9"
    assert score_band(9) == "6-9"
    assert score_band(10) == "10-14"
    assert score_band(14) == "10-14"
    assert score_band(15) == "15+"
    assert score_band(1000) == "15+"


def test_an_unscored_draft_gets_its_own_band() -> None:
    """Not the lowest band: never scored and badly scored are different statements."""
    assert score_band(None) == "unbewertet"


def test_response_rate_by_counts_only_sent_drafts() -> None:
    outcomes = [
        _outcome(draft_id="a", source="rss", sent=True, reply_kind=ReplyKind.INTERESTED),
        _outcome(draft_id="b", source="rss", sent=True, reply_kind=None),
        _outcome(draft_id="c", source="rss", sent=False, reply_kind=ReplyKind.INTERESTED),
    ]
    stats = response_rate_by(outcomes, lambda o: o.source)

    assert stats["rss"].total == 2  # the unsent draft does not count at all
    assert stats["rss"].positive == 1
    assert stats["rss"].rate == 0.5


def test_auto_reply_does_not_count_as_a_response() -> None:
    outcomes = [
        _outcome(draft_id="a", source="rss", sent=True, reply_kind=ReplyKind.AUTO_REPLY),
        _outcome(draft_id="b", source="rss", sent=True, reply_kind=ReplyKind.REJECTION),
    ]
    stats = response_rate_by(outcomes, lambda o: o.source)

    assert stats["rss"].total == 2
    assert stats["rss"].positive == 1  # only the rejection counts as an actual answer


def test_response_rate_by_groups_by_the_given_key() -> None:
    outcomes = [
        _outcome(draft_id="a", source="rss", sent=True, reply_kind=ReplyKind.INTERESTED),
        _outcome(draft_id="b", source="datei", sent=True, reply_kind=None),
    ]
    stats = response_rate_by(outcomes, lambda o: o.source)

    assert set(stats) == {"rss", "datei"}
    assert stats["datei"].rate == 0.0


def test_manual_edit_rate_counts_over_every_outcome_not_only_sent() -> None:
    outcomes = [
        _outcome(draft_id="a", sent=True, edited_by_hand=True),
        _outcome(draft_id="b", sent=False, edited_by_hand=True),
        _outcome(draft_id="c", sent=True, edited_by_hand=False),
    ]
    stat = manual_edit_rate(outcomes)

    assert stat.total == 3
    assert stat.positive == 2
    assert stat.rate == 2 / 3


def test_manual_edit_rate_of_an_empty_stream_is_zero_not_a_division_error() -> None:
    stat = manual_edit_rate([])
    assert stat.total == 0
    assert stat.rate == 0.0


def test_render_report_contains_every_breakdown_in_german() -> None:
    outcomes = [
        _outcome(draft_id="a", source="rss", score=8, signal_kind="eigenbau", variant="v1", sent=True,
                 reply_kind=ReplyKind.INTERESTED, edited_by_hand=True),
        _outcome(draft_id="b", source="datei", score=3, signal_kind=None, variant="v2", sent=True,
                 reply_kind=None, edited_by_hand=False),
    ]
    report = render_report(outcomes)

    assert "Antwortquote nach Quelle" in report
    assert "Antwortquote nach Punktband" in report
    assert "Antwortquote nach Anlasstyp" in report
    assert "Antwortquote nach Entwurfsvariante" in report
    assert "von Hand nachgebessert" in report
    assert "1/2" in report  # the honest metric: one of two drafts needed a fix
    assert "ohne Anlass" in report  # signal_kind=None must not disappear silently


def test_render_report_of_no_outcomes_does_not_crash() -> None:
    report = render_report([])
    assert "Keine Entwuerfe vorhanden." in report
