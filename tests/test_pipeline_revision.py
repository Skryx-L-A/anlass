"""The revision loop: measure, hand back instructions, rewrite, keep the best attempt.

The generator here is a stand-in that reacts to the notes it is given, so the loop can
be measured without a model: what is being tested is the loop, not a model's obedience.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from anlass.critique import RubricCritic
from anlass.critique.rubric import default_rubric
from anlass.draft.verify import GroundingVerifier
from anlass.errors import DraftError
from anlass.models import Draft, FieldValue, Lead, Paragraph, Signal
from anlass.pipeline import MAX_FORM_RETRIES, Pipeline, Stage, StepStatus
from anlass.store.sqlite import SqliteStore

from .conftest import SENDER

OCCASION = "Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung."

#: A draft that reaches every point for the lead below: it answers in the form the
#: posting uses, quotes the occasion, spreads its evidence, names something finished and
#: ends with a question.
GOOD_PARAGRAPHS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Hallo Roth, du schreibst, ihr baut eure Datenverarbeitung selbst und sucht "
        "dafuer Verstaerkung.",
        (),
    ),
    (
        "Ich habe eine Verarbeitungskette von 7,5 auf 3,8 Sekunden je Durchlauf "
        "gebracht, indem der Erkennungsschritt dauerhaft laeuft und nicht je Anfrage "
        "neu startet.",
        ("pipeline-latenz",),
    ),
    (
        # Deliberately long enough to clear the minimum on its own: since the sign-off
        # stopped counting towards length, a body that only just cleared sixty words
        # with the greeting added no longer does. The fixture used to pass on that
        # accident, so it is the fixture that was wrong, not the rule.
        "Das Werkzeug dazu laeuft auf Linux, Windows und macOS; die Testsuite deckt "
        "die oeffentlichen Funktionen ab, damit ein Umbau nichts still zerbricht. "
        "Gebaut habe ich es so, dass sich jeder Schritt einzeln austauschen laesst, "
        "und dokumentiert habe ich dabei auch die Stellen, an denen ich mich geirrt "
        "habe und noch einmal von vorn angefangen bin.",
        ("cli-crossplatform", "tests-abdeckung"),
    ),
    ("Schicke ich dir den Link auf das Repository? Viele Gruesse, Mara Lindqvist", ()),
]

#: Good, but not full: it ends in a phrase instead of a question. Two criteria short,
#: which is what a round has to be for a *worse* round to have something to fall from.
MIDDLING_PARAGRAPHS: list[tuple[str, tuple[str, ...]]] = GOOD_PARAGRAPHS[:3] + [
    ("Ich wuerde mich freuen, von dir zu hoeren. Viele Gruesse, Mara Lindqvist", ()),
]

#: The same content as application German: wrong salutation form, no finished result,
#: no evidence, no concrete ending. Nothing in it is invented, so stage 7 lets it pass -
#: which is exactly finding Q8, a draft that is verified and still bad.
POOR_PARAGRAPHS: list[tuple[str, tuple[str, ...]]] = [
    ("Sehr geehrte Damen und Herren,", ()),
    (
        "Hiermit bewerbe ich mich mit grossem Interesse; ich bin belastbar und arbeite "
        "hochmotiviert an allem, was anfaellt.",
        (),
    ),
    ("Viele Gruesse, Mara Lindqvist", ()),
]


def make_lead() -> Lead:
    lead = Lead(source="datei", source_ref="stelle.txt", text=OCCASION)
    lead.set("organization", FieldValue("Nordlicht Systeme", provider="datei"))
    lead.set("contact_name", FieldValue("Roth", provider="datei"))
    lead.set("form_of_address", FieldValue("du", provider="extraktion", confidence=0.8))
    return lead


def make_signal(lead: Lead) -> Signal:
    return Signal(lead_id=lead.id, kind="eigenbau", quote=OCCASION, detector="test")


@dataclass
class RevisingDrafter:
    """Answers badly first and well once it is handed notes.

    That is the whole contract with stage 6: an empty ``revision_notes`` is a first
    attempt, a non-empty one is a rewrite that has to work the notes off.
    """

    good: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=lambda: list(GOOD_PARAGRAPHS)
    )
    poor: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=lambda: list(POOR_PARAGRAPHS)
    )
    seen_notes: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "stub-ueberarbeitend"

    def draft(self, lead, signal, facts, voice="", revision_notes=()) -> Draft:
        self.seen_notes.append(tuple(revision_notes))
        paragraphs = self.good if revision_notes else self.poor
        return Draft(
            lead_id=lead.id,
            signal_id=None if signal is None else signal.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in paragraphs],
            model=self.name,
        )


@dataclass
class WorseningDrafter:
    """Every rewrite comes out worse than the attempt before it."""

    rounds: list[list[tuple[str, tuple[str, ...]]]] = field(default_factory=list)
    calls: int = 0

    @property
    def name(self) -> str:
        return "stub-verschlechternd"

    def draft(self, lead, signal, facts, voice="", revision_notes=()) -> Draft:
        paragraphs = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return Draft(
            lead_id=lead.id,
            signal_id=None if signal is None else signal.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in paragraphs],
            model=self.name,
        )


@dataclass
class OldDrafter:
    """A generator from before the seam: it knows no revision notes at all."""

    refuse: str = ""
    """Non-empty: refuse the one answer this generator gets, and hand the text out."""

    @property
    def name(self) -> str:
        return "stub-alt"

    def draft(self, lead, signal, facts, voice="") -> Draft:
        built = Draft(
            lead_id=lead.id,
            signal_id=None if signal is None else signal.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in POOR_PARAGRAPHS],
            model=self.name,
        )
        if self.refuse:
            raise DraftError(self.refuse, draft=built)
        return built


@dataclass
class FailingDrafter:
    """Works once, then breaks - a model whose second answer is unusable."""

    calls: int = 0

    @property
    def name(self) -> str:
        return "stub-kaputt"

    def draft(self, lead, signal, facts, voice="", revision_notes=()) -> Draft:
        self.calls += 1
        if self.calls > 1:
            raise DraftError("Das Modell lieferte kein auswertbares JSON-Objekt.")
        return Draft(
            lead_id=lead.id,
            signal_id=None if signal is None else signal.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in POOR_PARAGRAPHS],
            model=self.name,
        )


@dataclass
class RejectingDrafter:
    """Rejects its own answer, exactly as stage 6 does, then delivers.

    The real case (29.07.2026): the model answered with six paragraphs where four are
    allowed, :class:`anlass.draft.generate.FactGroundedDrafter` refused its own output,
    and the loop stopped with rounds left over.

    ``attach`` is the difference phase 7 introduced. Stage 6 refuses a **finished text**
    and hands it out with the refusal, so the default is ``True``. ``False`` is the other
    case, and it is a real one: unusable JSON, a call that timed out - a refusal with
    nothing behind it.
    """

    reason: str = "Das Modell lieferte 6 Absaetze, erlaubt sind hoechstens 4."
    fail_on: int = 1
    """Which call is the one the generator refuses. Everything else answers."""
    attach: bool = True
    always: bool = False
    """Refuse every single answer - the Nordlicht case, four rounds, four violations."""
    violation: str = ""
    """Which rule was broken, as stage 6 tags it since phase 8. Empty means "a different
    one each time": two rounds in a row on the *same* violation end the loop, so a stub
    that wants to exhaust the budget has to break a different rule every round - which is
    the ordinary case, six paragraphs first, then the wrong form of address."""
    seen_notes: list[tuple[str, ...]] = field(default_factory=list)
    calls: int = 0

    @property
    def name(self) -> str:
        return "stub-zurueckweisend"

    def draft(self, lead, signal, facts, voice="", revision_notes=()) -> Draft:
        self.calls += 1
        self.seen_notes.append(tuple(revision_notes))
        paragraphs = GOOD_PARAGRAPHS if revision_notes and not self.always else POOR_PARAGRAPHS
        if self.always:
            # Each attempt has to differ, or the loop stops on "unchanged" before the
            # budget is reached - and the budget is what these tests are about.
            head, ids = paragraphs[0]
            paragraphs = [(f"{head} Versuch {self.calls}.", ids)] + list(paragraphs[1:])
        built = Draft(
            lead_id=lead.id,
            signal_id=None if signal is None else signal.id,
            subject="Ihre Ausschreibung",
            paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in paragraphs],
            model=self.name,
        )
        if self.always or self.calls == self.fail_on:
            raise DraftError(
                self.reason,
                draft=built if self.attach else None,
                violation=self.violation or f"stub-{self.calls}",
            )
        return built


@pytest.fixture
def chain(facts):
    def build(drafter, **overrides) -> Pipeline:
        defaults = dict(
            facts=facts,
            drafter=drafter,
            verifier=GroundingVerifier(extra_grounding=(SENDER,)),
            critic=RubricCritic(),
        )
        defaults.update(overrides)
        return Pipeline(**defaults)

    return build


# ------------------------------------------------------- the loop the task asks for


def test_a_poor_draft_scores_low_produces_notes_and_the_rewrite_reaches_full_marks(chain):
    """The acceptance test: bad draft, notes, and a draft that follows them is full."""
    drafter = RevisingDrafter()
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert len(run.rounds) == 2
    first, second = run.rounds
    assert first.critique.is_full is False
    assert first.critique.notes, "eine offene Rubrik ohne Notiz waere nutzlos"
    assert second.notes == first.critique.notes, "die Notizen gehen woertlich zurueck"
    assert drafter.seen_notes[0] == ()
    assert drafter.seen_notes[1] == first.critique.notes

    assert second.critique.is_full is True
    assert run.critique.earned == default_rubric().possible
    assert run.draft is second.draft
    assert run.step(Stage.CRITIQUE).status is StepStatus.OK
    assert "nach 2 Runden" in run.step(Stage.CRITIQUE).detail


def test_the_notes_say_what_to_do_and_name_the_findings_of_the_first_round(chain):
    lead = make_lead()
    run = chain(RevisingDrafter()).draft_lead(lead, signal=make_signal(lead))
    notes = " ".join(run.rounds[0].critique.notes)

    assert "Du-Form" in notes
    assert "Bewerbungsdeutsch" in notes
    assert "fertiges, belegtes Ergebnis" in notes


def test_a_round_that_came_out_worse_is_discarded_and_ends_the_loop(chain):
    """A revision that harms is not repeated three times."""
    drafter = WorseningDrafter(rounds=[MIDDLING_PARAGRAPHS, POOR_PARAGRAPHS])
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert [entry.discarded for entry in run.rounds] == [False, True]
    assert drafter.calls == 2, "nach der schlechteren Runde wird nicht weiter versucht"
    assert run.draft is run.rounds[0].draft
    assert "verworfen" in run.rounds[1].line()
    assert "verworfen" in run.step(Stage.CRITIQUE).detail


def test_the_best_attempt_goes_on_not_the_last(chain):
    """Three rounds, the middle one is the best. The run keeps the middle one."""
    middle = list(GOOD_PARAGRAPHS)
    drafter = WorseningDrafter(rounds=[POOR_PARAGRAPHS, middle, POOR_PARAGRAPHS])
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert run.draft is run.rounds[1].draft
    assert run.rounds[1].critique.earned > run.rounds[0].critique.earned


def test_the_budget_is_respected_and_the_best_attempt_still_goes_to_the_person(chain):
    """Nothing reaches full marks. After the budget the best attempt goes on anyway.

    The three attempts differ only in their sign-off, so they score the same: what is
    measured here is the budget, not the ranking.
    """
    variants = [
        POOR_PARAGRAPHS[:2] + [(f"Viele Gruesse {tail}, Mara Lindqvist", ())]
        for tail in ("vorab", "und danke", "aus dem Norden")
    ]
    drafter = WorseningDrafter(rounds=variants)
    lead = make_lead()
    run = chain(drafter, max_revisions=2).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls == 3, "ein Erstversuch plus zwei Ueberarbeitungen"
    assert [entry.number for entry in run.rounds] == [1, 2, 3]
    assert run.draft is not None
    assert run.critique.is_full is False
    assert "offen" in run.step(Stage.CRITIQUE).detail
    assert "offenen Punkten an dich" in run.step(Stage.CRITIQUE).detail


def test_a_rewrite_that_changed_nothing_ends_the_loop(chain):
    """A model that answers the same thing twice will not answer differently the fourth
    time. Each further round would only cost a call."""
    drafter = WorseningDrafter(rounds=[POOR_PARAGRAPHS])
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls == 2
    assert run.rounds[1].discarded_because == "unveraendert gegenueber der Runde davor"
    assert run.draft is run.rounds[0].draft


def test_switching_the_revisions_off_leaves_the_measurement_standing(chain):
    lead = make_lead()
    run = chain(RevisingDrafter(), max_revisions=0).draft_lead(lead, signal=make_signal(lead))

    assert len(run.rounds) == 1
    assert run.critique.is_full is False
    assert run.step(Stage.CRITIQUE).status is StepStatus.OK


def test_a_generator_without_the_seam_is_used_once_and_the_run_says_so(chain):
    """No exception, no four identical drafts: one round, and it is named."""
    lead = make_lead()
    run = chain(OldDrafter()).draft_lead(lead, signal=make_signal(lead))

    assert len(run.rounds) == 1
    assert "keine Ueberarbeitungsnotizen" in run.step(Stage.CRITIQUE).detail
    assert run.draft is not None


def test_a_failing_rewrite_keeps_the_attempt_that_worked(chain):
    drafter = FailingDrafter()
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert len(run.rounds) == 1
    assert run.draft is run.rounds[0].draft
    assert run.step(Stage.DRAFT).status is StepStatus.OK
    assert "scheiterten" in run.step(Stage.DRAFT).detail
    assert drafter.calls == 4, "die gescheiterten Runden wurden im Budget erneut versucht"


def test_a_first_attempt_that_fails_uses_up_the_budget_and_then_ends_the_run(chain):
    """A generator that never delivers still gets its rounds - and then it is over.

    The retry is bounded by the same budget as everything else; there is no path here
    that keeps calling a model that cannot answer.
    """
    drafter = FailingDrafter(calls=1)
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert run.rounds == []
    # Der Zaehler startet hier auf 1, damit schon der erste Aufruf scheitert; die vier
    # Aufrufe darueber sind der Erstversuch plus drei Ueberarbeitungen.
    assert drafter.calls == 5
    assert run.stopped_at.stage is Stage.DRAFT
    assert run.stopped_at.status is StepStatus.FAILED


def test_a_rejected_round_comes_back_as_a_note_and_the_loop_goes_on(chain):
    """Phase 6, finding 2: the generator's own reason is the best note there is.

    Before this, the run ended here with no draft at all, although the reason for the
    rejection said precisely what to change and three rounds were still unspent.

    Phase 7 replaces one assertion of this test with a reason: it used to read
    ``[entry.number for entry in run.rounds] == [2]``, "Runde 1 gibt es nicht". Round 1
    does exist now - the refused text is kept and measured (see the tests below).
    """
    drafter = RejectingDrafter()
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert drafter.seen_notes[0] == (), "der Erstversuch bekommt keine Notizen"
    note = drafter.seen_notes[1][0]
    assert "6 Absaetze" in note, "der Grund geht woertlich zurueck, mit seinen Zahlen"
    assert "zurueckgewiesen" in note

    assert run.draft is not None
    assert run.critique.is_full is True
    assert [entry.number for entry in run.rounds] == [1, 2]
    assert run.rounds[0].rejected and not run.rounds[1].rejected
    assert run.draft is run.rounds[1].draft, "die saubere Runde gewinnt"
    assert "Eine Runde scheiterte" in run.step(Stage.DRAFT).detail


def test_a_refusal_without_a_text_keeps_the_open_criteria_of_the_last_real_attempt(chain):
    """Two sorts of note stack: why the last attempt was rejected, and what is open.

    Without the second half a rejection would wipe the review of the draft before it,
    and the rewrite would repair the paragraph count while falling back into the
    application German it had already been told about. ``attach=False`` is the case this
    is about: the answer was unusable, so there is no newer text to review and the older
    review is all there is.
    """
    drafter = RejectingDrafter(
        reason="Das Modell lieferte kein auswertbares JSON-Objekt.", fail_on=2, attach=False
    )
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    open_after_round_one = run.rounds[0].critique.notes
    assert open_after_round_one, "Runde 1 ist der schwache Entwurf, sie hat offene Punkte"
    last = drafter.seen_notes[-1]
    assert last[0].startswith("Der vorige Entwurf wurde zurueckgewiesen")
    assert last[1:] == open_after_round_one, "die offenen Kriterien stehen weiter dahinter"
    assert run.draft is not None


# ------------------------------------------- phase 7: a run never comes back empty


def test_every_round_refused_still_hands_over_the_best_text(chain):
    """The Nordlicht case, 29.07.2026: four rounds, four violations, no draft at all.

    Four model calls had produced four texts. Throwing them away is the worst possible
    outcome - there was nothing to look at and nothing to correct by hand. Now the best
    of them goes to the person with the violation named as the open point.
    """
    drafter = RejectingDrafter(
        reason="Die Ausschreibung verlangt hoechstens 3 Saetze, der Entwurf hat 6.",
        always=True,
    )
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert run.draft is not None, "vier Anlaeufe, vier Texte - einer davon geht raus"
    assert run.step(Stage.DRAFT).status is StepStatus.OK
    assert all(entry.rejected for entry in run.rounds)
    assert "hoechstens 3 Saetze" in run.step(Stage.DRAFT).detail
    assert "Offen bleibt eine harte Vorgabe" in run.step(Stage.CRITIQUE).detail
    assert "haelt eine harte Vorgabe nicht ein" in "\n".join(run.trace())


def test_a_refused_round_costs_no_revision_but_the_retries_are_bounded(chain):
    """A round that never got past a formality must not cost a round of polishing.

    The ceiling is the point of the test: free retries are :data:`MAX_FORM_RETRIES`, so
    a generator that refuses everything is called at most the quality budget plus two -
    a model that misses the same cap three times will not hit it on the fourth.

    Phase 8 narrows what this test can say, and the narrowing is the new rule, not a
    weakening: a generator that breaks the **same** rule twice running is stopped after
    the second round (see the test below), so this ceiling is only ever reached by one
    that breaks a different rule each time. The stub does that by default.
    """
    drafter = RejectingDrafter(reason="Zu viele Saetze.", always=True)
    lead = make_lead()
    run = chain(drafter, max_revisions=1).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls == 2 + MAX_FORM_RETRIES
    assert len(run.rounds) == drafter.calls
    assert run.draft is not None


def test_the_same_violation_twice_running_ends_the_loop(chain):
    """Phase 8: five rounds on one violation are five model calls spent on nothing.

    Measured on 29.07.2026 over four fresh runs: five of six rounds against the Talwerk
    posting broke the same two-sentence bound, after being told about it five times
    (Q19). An instruction that is not followed at the second attempt is not followed at
    the sixth either. The round is still kept and still handed over - what stops is the
    asking.
    """
    drafter = RejectingDrafter(
        reason="Die Ausschreibung verlangt hoechstens 2 Saetze, der Entwurf hat 3.",
        always=True,
        violation="satzanzahl",
    )
    lead = make_lead()
    run = chain(drafter, max_revisions=3).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls == 2, "nach der zweiten gleichen Verletzung wird nicht weitergefragt"
    assert len(run.rounds) == 2
    assert run.draft is not None, "der beste Versuch geht trotzdem raus"
    assert "derselben Verletzung" in run.step(Stage.DRAFT).detail
    assert "Offen bleibt eine harte Vorgabe" in run.step(Stage.CRITIQUE).detail


def test_two_different_violations_do_not_end_the_loop(chain):
    """The counterpart, and the reason the rule compares the rule and not "a rejection".

    Two rounds that broke two different rules are a loop making progress: the first note
    was followed, a second defect surfaced. Stopping there would throw away the rounds
    that fix it.
    """
    drafter = RejectingDrafter(reason="Zu viele Saetze.", always=True)
    lead = make_lead()
    run = chain(drafter, max_revisions=3).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls > 2
    assert "derselben Verletzung" not in run.step(Stage.DRAFT).detail


def test_an_untagged_generator_is_stopped_by_its_repeated_message(chain):
    """A generator that does not name the rule is compared by its message, digits masked.

    "der Entwurf hat 3" and "der Entwurf hat 4" are the same defect written twice, so the
    numbers cannot be part of the comparison. Without this the rule would only work for
    the generator that ships with the package.
    """

    @dataclass
    class UntaggedDrafter(RejectingDrafter):
        def draft(self, lead, signal, facts, voice="", revision_notes=()) -> Draft:
            self.calls += 1
            built = Draft(
                lead_id=lead.id,
                signal_id=None if signal is None else signal.id,
                paragraphs=[Paragraph(text=f"Versuch {self.calls}.", fact_ids=())],
                model=self.name,
            )
            raise DraftError(
                f"Die Ausschreibung verlangt hoechstens 2 Saetze, der Entwurf hat "
                f"{2 + self.calls}.",
                draft=built,
            )

    drafter = UntaggedDrafter()
    lead = make_lead()
    run = chain(drafter, max_revisions=3).draft_lead(lead, signal=make_signal(lead))

    assert drafter.calls == 2
    assert "derselben Verletzung" in run.step(Stage.DRAFT).detail


def test_a_clean_round_beats_a_refused_one_even_with_fewer_points(chain):
    """Breaking the posting's first instruction loses the application before it is read.

    So the ranking asks in this order: is it grounded, does it keep the rules, how many
    points. A refused round only ever wins when nothing else exists.
    """
    drafter = RejectingDrafter(reason="Zu viele Saetze.", fail_on=1)
    lead = make_lead()
    run = chain(drafter, max_revisions=1).draft_lead(lead, signal=make_signal(lead))

    refused, clean = run.rounds
    assert refused.rejected and not clean.rejected
    assert run.draft is clean.draft
    assert "harte Vorgabe" not in run.step(Stage.CRITIQUE).detail


def test_a_generator_without_the_seam_still_hands_its_refused_text_over(chain):
    """One attempt is all it gets - but one attempt is one text.

    The notes seam decides whether there is a second round, not whether the first one is
    worth anything. Without this, the case that cannot be revised at all is also the one
    that comes back empty, which is the wrong way round.
    """
    run = chain(OldDrafter(refuse="Zu viele Saetze.")).draft_lead(
        make_lead(), signal=make_signal(make_lead())
    )

    assert len(run.rounds) == 1
    assert run.draft is not None
    assert run.rounds[0].rejected
    assert run.step(Stage.DRAFT).status is StepStatus.OK


def test_a_refused_text_is_written_down_like_any_other(chain, tmp_path):
    """It is what the person will be shown, so ``anlass review`` has to find it."""
    store = SqliteStore(tmp_path / "lauf.db")
    store.migrate()
    try:
        lead = make_lead()
        store.save_lead(lead)
        drafter = RejectingDrafter(reason="Zu viele Saetze.", always=True)
        run = chain(drafter, store=store).draft_lead(lead, signal=make_signal(lead))

        stored = {draft.id for draft in store.list_drafts()}
        assert run.draft.id in stored
    finally:
        store.close()


# ------------------------------------------------------------ grounding in the loop


def test_an_invented_number_comes_back_as_a_revision_note(chain):
    """Rule 2 of the project inside the loop: the fabrication is the first note."""
    liar = [("Wir haben die Antwortzeit auf 99 Millisekunden gedrueckt.", ())]
    drafter = WorseningDrafter(rounds=[liar, list(GOOD_PARAGRAPHS)])
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    first = run.rounds[0]
    assert first.verification.passed is False
    assert "99" in first.critique.notes[0]
    assert first.critique.is_full is False, "unbelegt ist nie voll, egal wie viele Punkte"


def test_a_grounded_attempt_beats_a_higher_scoring_ungrounded_one(chain):
    """Truth first: a draft that invents something must not win on style points."""
    liar = [
        ("Sehr geehrte Frau Roth, ihr baut eure Datenverarbeitung selbst.", ()),
        ("Wir haben die Antwortzeit auf 99 Millisekunden gedrueckt.", ()),
        ("Schickst du mir eine Zeit fuer ein Gespraech?", ()),
    ]
    thin = [("Die Verarbeitungskette laeuft in 3,8 Sekunden je Durchlauf.", ("pipeline-latenz",))]
    drafter = WorseningDrafter(rounds=[thin, liar])
    lead = make_lead()
    run = chain(drafter).draft_lead(lead, signal=make_signal(lead))

    assert run.draft is run.rounds[0].draft
    assert run.verification.passed is True


# -------------------------------------------------------------- protocol and store


def test_the_run_protocol_shows_the_rounds(chain):
    """Replaces the version that asserted "Runde 1/4" (phase 7).

    The denominator is the ceiling on model calls, and since a round refused over a
    countable formality no longer costs a revision, that ceiling is the quality budget
    plus :data:`anlass.pipeline.MAX_FORM_RETRIES`. Written as the sum rather than as the
    literal 6, so the line stays true if either number is changed.
    """
    lead = make_lead()
    run = chain(RevisingDrafter()).draft_lead(lead, signal=make_signal(lead))
    text = "\n".join(run.trace())
    total = 1 + Pipeline.max_revisions + MAX_FORM_RETRIES

    assert f"Entwurf Runde 1/{total}" in text
    assert f"Entwurf Runde 2/{total}" in text
    assert "Punkten" in text
    assert "Guete" in text


def test_every_attempt_is_written_down_not_only_the_one_that_was_kept(chain, tmp_path):
    """The store is the only place a discarded attempt survives the process."""
    store = SqliteStore(tmp_path / "lauf.db")
    store.migrate()
    try:
        lead = make_lead()
        store.save_lead(lead)
        run = chain(RevisingDrafter(), store=store).draft_lead(lead, signal=make_signal(lead))

        stored = {draft.id for draft in store.list_drafts()}
        assert {entry.draft.id for entry in run.rounds} <= stored
        assert store.latest_verification(run.draft.id) is not None
    finally:
        store.close()


def test_without_a_critic_nothing_is_measured_and_the_run_says_so(chain):
    lead = make_lead()
    run = chain(RevisingDrafter(), critic=None).draft_lead(lead, signal=make_signal(lead))

    assert run.critique is None
    assert run.step(Stage.CRITIQUE).status is StepStatus.MISSING
    assert "misst" in run.step(Stage.CRITIQUE).detail
    assert len(run.rounds) == 1
