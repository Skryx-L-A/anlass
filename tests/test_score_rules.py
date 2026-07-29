"""Stage 4: RuleScorer against a criteria dict - no model involved anywhere here."""

from __future__ import annotations

import pytest

from anlass.errors import ConfigError
from anlass.models import FieldValue, Lead
from anlass.score.rules import RuleScorer

CRITERIA: dict = {
    "criteria": [
        {"name": "remote_or_nearby", "weight": 3, "check": "remote == true"},
        {"name": "build_it_yourself", "weight": 3, "check": "text_contains_any([bauen, eigenverantwortlich])"},
        {"name": "small_org", "weight": 2, "check": "headcount < 200"},
        {"name": "contact_known", "weight": 1, "check": "contact_name != null"},
    ],
    "exclusions": [
        "employment_type == vollzeit_unbefristet",
    ],
    "limits": {"max_sends_per_day": 5, "days_between_same_organization": 30, "min_score": 6},
}


def _lead(**fields: object) -> Lead:
    entry = Lead(source="datei", text="Wir bauen unsere Datenverarbeitung selbst.")
    for name, value in fields.items():
        entry.set(name, FieldValue(value, provider="test"))
    return entry


def test_score_is_the_sum_of_passed_criteria_weights():
    scorer = RuleScorer(CRITERIA)
    lead = _lead(remote=True, headcount=50, contact_name="Roth")
    result = scorer.score(lead)
    assert result.total == 3 + 3 + 2 + 1
    assert result.accepted is True
    assert result.excluded_by is None
    assert len(result.outcomes) == 4
    assert all(o.passed for o in result.outcomes)


def test_every_outcome_carries_a_reason_naming_its_criterion():
    scorer = RuleScorer(CRITERIA)
    entry = Lead(source="datei", text="Eine ganz gewoehnliche Ausschreibung ohne besondere Merkmale.")
    result = scorer.score(entry)  # no field set, and the text matches no keyword either
    assert result.total == 0
    names = {o.name for o in result.outcomes}
    assert names == {"remote_or_nearby", "build_it_yourself", "small_org", "contact_known"}
    for outcome in result.outcomes:
        assert outcome.passed is False
        assert outcome.name in outcome.reason


def test_a_lead_under_the_minimum_score_is_excluded_here_not_later():
    """The Scorer promise from anlass.interfaces: excluded at stage 4, not hoped away."""
    scorer = RuleScorer(CRITERIA)
    lead = _lead(remote=False, headcount=5000)  # scores 0, well under min_score 6
    result = scorer.score(lead)
    assert result.accepted is False
    assert result.excluded_by is not None
    assert "score" in result.excluded_by


def test_min_score_is_enforced_even_without_an_explicit_score_exclusion_rule():
    """The exclusion list may forget to mirror limits.min_score; the guard still fires."""
    criteria = {
        "criteria": [{"name": "only", "weight": 2, "check": "remote == true"}],
        "exclusions": [],  # deliberately no "score < N" entry
        "limits": {"max_sends_per_day": 5, "days_between_same_organization": 30, "min_score": 6},
    }
    scorer = RuleScorer(criteria)
    result = scorer.score(_lead(remote=True))  # totals 2, below min_score 6
    assert result.total == 2
    assert result.accepted is False
    assert "min_score" in (result.excluded_by or "")


def test_explicit_exclusion_names_the_criterion_that_caused_it():
    scorer = RuleScorer(CRITERIA)
    lead = _lead(remote=True, headcount=50, contact_name="Roth", employment_type="vollzeit_unbefristet")
    result = scorer.score(lead)
    assert result.total >= 6  # would pass on points alone
    assert result.accepted is False
    assert result.excluded_by == "employment_type == vollzeit_unbefristet"


def test_first_matching_exclusion_wins():
    criteria = {
        "criteria": [{"name": "only", "weight": 10, "check": "remote == true"}],
        "exclusions": ["headcount > 100", "employment_type == vollzeit_unbefristet"],
        "limits": {"max_sends_per_day": 5, "days_between_same_organization": 30, "min_score": 1},
    }
    scorer = RuleScorer(criteria)
    lead = _lead(remote=True, headcount=5000, employment_type="vollzeit_unbefristet")
    result = scorer.score(lead)
    assert result.excluded_by == "headcount > 100"


def test_real_shipped_example_criteria_file_scores_a_matching_lead():
    from anlass.profile import load_profile

    profile = load_profile("profile.example")
    scorer = RuleScorer(profile.criteria)
    lead = _lead(remote=True, employment_type="werkstudent", headcount=50, contact_name="Roth")
    result = scorer.score(lead)
    assert result.accepted is True
    assert result.total > 0


@pytest.mark.parametrize(
    "criteria",
    [
        {"criteria": []},
        {"criteria": [{"weight": 1, "check": "remote == true"}]},  # no name
        {"criteria": [{"name": "x", "check": "remote == true"}]},  # no weight
        {"criteria": [{"name": "x", "weight": 1}]},  # no check
        {"criteria": [{"name": "x", "weight": "viel", "check": "remote == true"}]},
        {"criteria": [{"name": "dup", "weight": 1, "check": "remote == true"}, {"name": "dup", "weight": 1, "check": "headcount < 10"}]},
    ],
)
def test_malformed_criteria_file_raises_config_error(criteria: dict):
    with pytest.raises(ConfigError):
        RuleScorer(criteria)


def test_scorer_name_is_stable():
    scorer = RuleScorer(CRITERIA, name="mein-regelwerk")
    assert scorer.name == "mein-regelwerk"
