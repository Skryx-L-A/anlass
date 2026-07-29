"""The closed expression evaluator: parsing, resolution, the operations it allows."""

from __future__ import annotations

import pytest

from anlass.errors import ConfigError
from anlass.models import FieldValue, Lead
from anlass.score.expr import CheckExpression


def _lead(**fields: object) -> Lead:
    entry = Lead(source="datei", text="Wir bauen unsere Datenverarbeitung selbst.")
    for name, value in fields.items():
        entry.set(name, FieldValue(value, provider="test"))
    return entry


def test_multiword_bareword_list_items_are_kept_verbatim():
    # "from scratch" is two words with no operator between them - not valid Python -
    # which is exactly why this needs its own tokenizer instead of ast.parse.
    check = CheckExpression("text_contains_any([bauen, implementieren, from scratch])")
    assert check.evaluate(_lead()) is True
    assert check.evaluate(_lead(), {}) is True
    no_match = CheckExpression("text_contains_any([kaffeetasse, buerostuhl])")
    assert no_match.evaluate(_lead()) is False


def test_field_reference_reads_the_lead():
    check = CheckExpression("remote == true")
    assert check.evaluate(_lead(remote=True)) is True
    assert check.evaluate(_lead(remote=False)) is False


def test_known_field_absent_resolves_to_none_not_to_its_own_name():
    # Regression: an earlier version resolved an unset field name to the identifier
    # text itself, which made "contact_name != null" true for every lead, set or not.
    check = CheckExpression("contact_name != null")
    assert check.evaluate(_lead()) is False
    assert check.evaluate(_lead(contact_name="Roth")) is True


def test_bare_constant_compares_as_a_literal_string():
    check = CheckExpression("employment_type == vollzeit_unbefristet")
    assert check.evaluate(_lead(employment_type="vollzeit_unbefristet")) is True
    assert check.evaluate(_lead(employment_type="werkstudent")) is False


def test_in_operator_over_a_list_literal():
    check = CheckExpression("employment_type in [werkstudent, praktikum, teilzeit]")
    assert check.evaluate(_lead(employment_type="werkstudent")) is True
    assert check.evaluate(_lead(employment_type="vollzeit_unbefristet")) is False


def test_numeric_comparison_and_or():
    check = CheckExpression("headcount < 200 or text_contains_any([startup])")
    assert check.evaluate(_lead(headcount=50)) is True
    assert check.evaluate(_lead(headcount=5000)) is False


def test_missing_numeric_field_fails_the_comparison_without_raising():
    check = CheckExpression("headcount < 200")
    assert check.evaluate(_lead()) is False


def test_and_not_grouping():
    check = CheckExpression("not (headcount > 200) and remote == true")
    assert check.evaluate(_lead(headcount=10, remote=True)) is True
    assert check.evaluate(_lead(headcount=1000, remote=True)) is False


def test_context_overrides_lead_and_supplies_extra_names():
    check = CheckExpression("distance_km(location, home_locations) < 60")
    lead = _lead(location="Beispielstadt")
    assert check.evaluate(lead, {"home_locations": ["Beispielstadt", "Berlin"]}) is True
    assert check.evaluate(lead, {"home_locations": ["Muenchen"]}) is False
    assert check.evaluate(lead) is False  # no context at all: never crashes


@pytest.mark.parametrize(
    "source",
    [
        "unknown_function(x)",
        "text_contains_any([a) ",
        "headcount <",
        "(headcount < 200",
        "",
    ],
)
def test_malformed_expression_raises_config_error_at_construction(source: str):
    with pytest.raises(ConfigError):
        CheckExpression(source)


def test_string_and_number_literals():
    assert CheckExpression('role == "Werkstudent"').evaluate(_lead(role="Werkstudent")) is True
    assert CheckExpression("headcount == 50").evaluate(_lead(headcount=50)) is True


def test_case_insensitive_string_equality():
    check = CheckExpression("employment_type == Werkstudent")
    assert check.evaluate(_lead(employment_type="werkstudent")) is True


def test_quoted_list_items_lose_their_quotes():
    """A quoted term must search for the word, not for the word plus apostrophes.

    Regression: the tokenizer used to keep the quotes, so ['bauen'] searched for the
    six characters 'bauen' including both apostrophes and matched nothing. It failed
    silently - the criterion never fired and the lead just scored low.
    """
    lead = _lead()
    assert CheckExpression("text_contains_any(['bauen'])").evaluate(lead) is True
    assert CheckExpression('text_contains_any(["bauen"])').evaluate(lead) is True
    assert CheckExpression("text_contains_any([bauen])").evaluate(lead) is True
    assert CheckExpression("text_contains_any(['fliegen'])").evaluate(lead) is False


def test_a_term_with_an_inner_apostrophe_survives():
    lead = Lead(source="datei", text="Wir suchen jemanden fuer O'Reilly-Themen.")
    assert CheckExpression("text_contains_any([\"O'Reilly\"])").evaluate(lead) is True
