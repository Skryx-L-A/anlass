"""``anlass review`` shows the score, the open criteria and which attempt this is.

The installation is the one ``anlass init`` writes, with the shipped stand-in as the
model. Nothing here reaches the network.
"""

from __future__ import annotations

import pytest

from anlass.models import Draft, Paragraph
from anlass.setup import Paths
from anlass.setup.sample import SAMPLE_FACTS, STAND_IN_DRAFT, SampleSource
from anlass.store.sqlite import SqliteStore

from .test_cli_commands import _write_facts, init_into, run


@pytest.fixture
def installed(tmp_path):
    init_into(tmp_path, STAND_IN_DRAFT)
    _write_facts(Paths(home=tmp_path))
    return tmp_path


def store_draft(home, paragraphs) -> Draft:
    paths = Paths(home=home)
    store = SqliteStore(paths.database)
    store.migrate()
    try:
        lead = SampleSource().normalize(next(SampleSource().fetch()))
        store.save_lead(lead)
        draft = Draft(
            lead_id=lead.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=text, fact_ids=ids) for text, ids in paragraphs],
            model="fake:test",
        )
        store.save_draft(draft)
        return draft
    finally:
        store.close()


def test_review_names_the_score_and_every_open_criterion(installed):
    draft = store_draft(
        installed,
        [
            ("Sehr geehrte Damen und Herren,", ()),
            (
                "Hiermit bewerbe ich mich mit grossem Interesse; ich bin belastbar und "
                "arbeite hochmotiviert an allem, was bei Ihnen anfaellt.",
                (),
            ),
            ("Viele Gruesse, Jonna Reuter", ()),
        ],
    )

    code, output = run(["--config", str(installed), "review", "--entwurf", draft.id])

    assert code == 0, output
    assert "Guete:" in output and "Punkten" in output
    assert "keine_floskeln" in output
    assert "hiermit bewerbe ich mich" in output, "die Notiz sagt, was zu tun ist"
    assert "Entwurf 1 von 1 zu diesem Lead" in output


def test_review_still_shows_the_draft_when_the_profile_is_missing(installed):
    """Looking at a draft has to work on a half-finished installation."""
    (Paths(home=installed).profile / "facts.yaml").unlink()
    draft = store_draft(installed, [("Ein Satz ohne Beleg.", ())])

    code, output = run(["--config", str(installed), "review", "--entwurf", draft.id])

    assert code == 0, output
    assert "Ein Satz ohne Beleg." in output
    assert "Die Guete liess sich nicht messen" in output


def test_review_counts_the_attempts_that_exist_for_the_lead(installed):
    first = store_draft(installed, [("Erster Versuch, ohne Beleg.", ())])
    paths = Paths(home=installed)
    store = SqliteStore(paths.database)
    store.migrate()
    try:
        second = Draft(
            lead_id=first.lead_id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text="Zweiter Versuch, ohne Beleg.", fact_ids=())],
            model="fake:test",
        )
        store.save_draft(second)
    finally:
        store.close()

    code, output = run(["--config", str(installed), "review", "--entwurf", second.id])

    assert code == 0, output
    assert "Entwurf 2 von 2 zu diesem Lead" in output


def test_review_measures_against_the_occasion_the_draft_was_written_for(installed):
    """Q13: the lead's oldest occasion is not the one this draft answered.

    Two occasions on one lead, the draft carries the second. Measured on the real
    Nordlicht draft before the fix: 20 of 26 points instead of 24, and a note asking it
    to take up a sentence from a run it never saw.
    """
    from anlass.models import Signal

    draft = store_draft(installed, [("Ein Satz ohne Beleg.", ())])
    paths = Paths(home=installed)
    store = SqliteStore(paths.database)
    store.migrate()
    try:
        lead = store.get_lead(draft.lead_id)
        # Both quotes really stand in the posting, so the note names whichever one was
        # measured against - that is what makes the assertion below able to fail.
        old = Signal(lead_id=lead.id, kind="alt", quote="Python ist unser Hauptwerkzeug.", detector="test")
        answered = Signal(
            lead_id=lead.id,
            kind="neu",
            quote="Wir sind vierzehn Leute.",
            detector="test",
        )
        store.save_signal(old)
        store.save_signal(answered)
        newest = Draft(
            lead_id=lead.id,
            signal_id=answered.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text="Ein Satz ohne Beleg.", fact_ids=())],
            model="fake:test",
        )
        store.save_draft(newest)
    finally:
        store.close()

    code, output = run(["--config", str(installed), "review", "--entwurf", newest.id])

    assert code == 0, output
    assert "Wir sind vierzehn Leute" in output, "gemessen wird gegen den eigenen Anlass"
    assert "Python ist unser Hauptwerkzeug" not in output, "nicht gegen den aeltesten"


def test_the_shipped_fact_base_is_enough_for_the_criterion_about_shipped_work(installed):
    """A draft that names a finished result keeps that point - the measure is passable."""
    shipped = next(fact for fact in SAMPLE_FACTS if "kette-dauerbetrieb" == fact.id)
    draft = store_draft(
        installed,
        [("Aus der naechtlichen Skriptsammlung wurde eine dauerhaft laufende Kette.",
          (shipped.id,))],
    )

    code, output = run(["--config", str(installed), "review", "--entwurf", draft.id])

    assert code == 0, output
    assert "ausgeliefertes_genannt" not in output
