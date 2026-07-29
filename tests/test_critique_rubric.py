"""The rubric: what it counts, what it refuses to count, and what it reads from a file.

Every draft in this file is invented. No model runs here - the whole point of the
deterministic layer is that it works without one.
"""

from __future__ import annotations

import pytest

from anlass.critique.rubric import (
    DraftContext,
    Rubric,
    default_rubric,
    is_strength,
    load_rubric,
    rubric_for_profile,
)
from anlass.errors import ConfigError
from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph, Signal

from .conftest import CLEAN_PARAGRAPHS

POSTING = (
    "Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung. "
    "Deine Aufgaben: Du baust Auswertungen und pflegst sie. Ansprechpartnerin ist Frau Roth."
)
OCCASION = "Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung."


def make_lead(**fields) -> Lead:
    lead = Lead(source="datei", source_ref="stelle.txt", text=POSTING)
    for name, value in fields.items():
        lead.set(name, FieldValue(value, provider="extraktion", confidence=0.8))
    return lead


def make_signal(lead: Lead, quote: str = OCCASION) -> Signal:
    return Signal(lead_id=lead.id, kind="eigenbau", quote=quote, detector="test")


def context(paragraphs, *, lead=None, signal=None, facts=(), verification=None) -> DraftContext:
    lead = make_lead() if lead is None else lead
    draft = Draft(
        lead_id=lead.id,
        subject="Ihre Ausschreibung",
        paragraphs=[Paragraph(text=text, fact_ids=ids) for text, ids in paragraphs],
        model="fake:test",
    )
    return DraftContext(
        draft=draft,
        lead=lead,
        signal=make_signal(lead) if signal is None else signal,
        facts=facts,
        verification=verification,
    )


def judge(ctx: DraftContext, rubric: Rubric | None = None) -> dict[str, tuple[int, str]]:
    """Criterion name to (points, note). One call, everything readable at once."""
    rubric = rubric or default_rubric()
    return {result.name: (result.earned, result.note) for result in rubric.judge(ctx)}


# -------------------------------------------------------------------- the clean case


def test_a_clean_draft_reaches_every_point(facts, lead, signal):
    """The yardstick. If a good draft loses points, every note below is noise."""
    draft = Draft(
        lead_id=lead.id,
        signal_id=signal.id,
        subject="Ihre Ausschreibung",
        paragraphs=[Paragraph(text=t, fact_ids=i) for t, i in CLEAN_PARAGRAPHS],
        model="fake:test",
    )
    rubric = default_rubric()
    results = rubric.judge(
        DraftContext(draft=draft, lead=lead, signal=signal, facts=facts)
    )

    assert [r.name for r in results if not r.full] == []
    assert sum(r.earned for r in results) == rubric.possible


def test_a_criterion_nobody_asked_for_costs_nothing(facts, lead, signal):
    """No form demanded means full marks for the form - not a punishment for silence."""
    ctx = context([("Ein kurzer, belegter Absatz ueber die Kette.", ("pipeline-latenz",))],
                  lead=make_lead(), facts=facts)
    assert judge(ctx)["form_eingehalten"][0] > 0


# ------------------------------------------------------- Q1: the demanded form


def test_the_demanded_sentence_count_is_an_upper_bound_not_a_suggestion(facts):
    lead = make_lead(max_sentences=2)
    ctx = context(
        [("Erster Satz. Zweiter Satz. Dritter Satz. Vierter Satz.", ("pipeline-latenz",))],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points == 0
    assert "hoechstens 2 Saetze" in note and "es sind 4" in note


def test_no_cover_letter_means_no_cover_letter(facts):
    """Talwerk wrote 'Keine Anschreiben' and got one. That is what this catches."""
    lead = make_lead(forbidden_artifacts=["Anschreiben"])
    ctx = context(
        [
            ("Sehr geehrte Damen und Herren,", ()),
            ("Ich habe die Kette von 7,5 auf 3,8 Sekunden gebracht.", ("pipeline-latenz",)),
            ("Viele Gruesse, Mara Lindqvist", ()),
        ],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points == 0
    assert "kein Anschreiben" in note


def test_the_same_forbidden_form_passes_without_salutation_and_sign_off(facts):
    lead = make_lead(forbidden_artifacts=["Anschreiben"])
    ctx = context(
        [("Ich habe die Kette von 7,5 auf 3,8 Sekunden gebracht. Der Code liegt offen.",
          ("pipeline-latenz",))],
        lead=lead,
        facts=facts,
    )
    assert judge(ctx)["form_eingehalten"][0] > 0


def test_a_demanded_artifact_has_to_appear(facts):
    lead = make_lead(required_artifacts=["Repository-Link", "Video"])
    ctx = context([("Ich habe die Kette schneller gemacht.", ("pipeline-latenz",))],
                  lead=lead, facts=facts)
    points, note = judge(ctx)["form_eingehalten"]

    assert points == 0
    assert "Video" in note


# ------------------------------------------------------------- Q2: the salutation


def test_a_posting_that_says_du_gets_answered_with_du(facts):
    lead = make_lead(form_of_address="du", contact_name="Roth")
    ctx = context(
        [("Sehr geehrte Frau Roth, Ihre Ausschreibung habe ich gelesen.", ())],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["anrede_passend"]

    assert points == 0
    assert "Du-Form" in note
    # The words that are in the way, not just the form that is wanted: a note has to
    # say where to act (phase 6, finding 3).
    assert "'Ihre'" in note


def test_the_du_form_passes_when_it_is_used(facts):
    lead = make_lead(form_of_address="du", contact_name="Roth")
    ctx = context(
        [("Hallo Roth, du baust eure Datenverarbeitung selbst - das habe ich auch gemacht.", ())],
        lead=lead,
        facts=facts,
    )
    assert judge(ctx)["anrede_passend"][0] > 0


def test_the_plural_du_form_counts_as_duzen(facts):
    """A company is duzt in the plural: "ihr", "euch", "eure".

    Measured on 29.07.2026 against the Talwerk posting: the draft answered "Hallo
    Mira" and asked "Woran wuerdet ihr mich zuerst ransetzen?", which is the informal
    form, and still lost all four points with the note "Schreibe die Anrede in
    Du-Form".
    """
    lead = make_lead(form_of_address="du", contact_name="Roth")
    ctx = context(
        [(
            "Hallo Roth, ihr baut eure Datenverarbeitung selbst - woran wuerdet ihr "
            "mich zuerst ransetzen?",
            (),
        )],
        lead=lead,
        facts=facts,
    )
    assert judge(ctx)["anrede_passend"][0] > 0


def test_a_formal_letter_to_a_posting_that_duzt_still_fails(facts):
    """The counterpart: the wider list must not let "Ihre Ausschreibung" through.

    Folded to lower case, the polite "Ihre" and the possessive "ihre" are the same
    word, so the informal evidence alone would accept a formal letter. The Sie-forms
    are read from the unfolded text, where they are still capitalised, and they decide.
    """
    lead = make_lead(form_of_address="du", contact_name="Roth")
    ctx = context(
        [("Sehr geehrte Frau Roth, Ihre Ausschreibung habe ich gelesen.", ())],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["anrede_passend"]

    assert points == 0
    assert "'Ihre'" in note


def test_a_known_name_is_used(facts):
    lead = make_lead(form_of_address="sie", contact_name="Roth")
    ctx = context([("Sehr geehrte Damen und Herren, Ihre Ausschreibung habe ich gelesen.", ())],
                  lead=lead, facts=facts)
    points, note = judge(ctx)["anrede_passend"]

    assert points == 0
    assert "Roth" in note


# ---------------------------------------------------------------- Q3: the occasion


def test_an_occasion_that_is_not_in_the_posting_does_not_count():
    lead = make_lead()
    invented = make_signal(lead, quote="Wir suchen jemanden fuer unsere Raumfahrtabteilung.")
    ctx = context([("Ihre Raumfahrtabteilung klingt spannend.", ())], lead=lead, signal=invented)
    points, note = judge(ctx)["anlass_konkret"]

    assert points == 0
    assert "wirklich in der Ausschreibung" in note


def test_an_occasion_that_is_only_alluded_to_does_not_count():
    """A letter, so the verbatim pick-up stays demanded - see the short form below."""
    ctx = context([("Ihre Ausschreibung hat mich sehr angesprochen.", ())])
    points, note = judge(ctx)["anlass_konkret"]

    assert points == 0
    assert "woertlich" in note


#: The Nordlicht occasion and the form it demands (29.07.2026). Both are what
#: the two tests below are about, so they stand here as data rather than inline.
NORDLICHT_OCCASION = (
    "Du hast nachweislich schon etwas mit KI gebaut oder automatisiert "
    "(GPT, n8n, eigene Skripte)."
)
NORDLICHT_FORM = "3 Saetze zu zuletzt mit KI gebautem/automatisiertem"


def test_a_short_form_answers_the_occasion_instead_of_quoting_it():
    """Phase 7: 14 quoted words are a third of a three-sentence answer.

    The posting asks "beschreibe, welche KI-Projekte du zuletzt gebaut hast". A draft
    that says what it built has taken the occasion up; one that repeats the question has
    only spent its budget. Measured on 29.07.2026: the note demanding the quote drove
    the generator over the sentence cap four rounds running.
    """
    lead = make_lead(max_sentences=3)
    lead.text = lead.text + " " + NORDLICHT_OCCASION
    signal = make_signal(lead, quote=NORDLICHT_OCCASION)
    ctx = context(
        [
            (
                "Ich habe mit Claude eine Workbench gebaut, die Aufgaben an parallel "
                "laufende Agenten verteilt.",
                ("pipeline-latenz",),
            )
        ],
        lead=lead,
        signal=signal,
    )
    points, note = judge(ctx)["anlass_konkret"]

    assert points > 0, note
    assert NORDLICHT_OCCASION not in ctx.text, "das Zitat steht bewusst nicht im Entwurf"


def test_the_floor_does_not_guess_whether_a_short_form_is_on_topic():
    """Measured, and the reason the floor says nothing instead of picking a threshold.

    The good answer above shares one of the occasion's eight content words, a draft
    about something else entirely shares none. 0,125 against 0 does not separate them,
    so no number here would measure anything but this one draft.
    """
    lead = make_lead(max_sentences=3)
    lead.text = lead.text + " " + NORDLICHT_OCCASION
    signal = make_signal(lead, quote=NORDLICHT_OCCASION)
    ctx = context(
        [("Ich wohne in der Naehe und kann sofort anfangen.", ())], lead=lead, signal=signal
    )

    assert judge(ctx)["anlass_konkret"][0] > 0, "der Boden schweigt, er raet nicht"
    assert default_rubric().criterion("anlass_konkret").model_checkable, (
        "aufgegeben hat der Boden die Frage, nicht die Rubrik - die Modellschicht "
        "darf sie weiterhin abziehen"
    )


def test_a_short_form_still_needs_an_occasion_that_stands_in_the_posting():
    """What the floor keeps for a short form: there is one, and it is not invented."""
    lead = make_lead(max_sentences=3)
    invented = make_signal(lead, quote="Wir suchen jemanden fuer unsere Raumfahrtabteilung.")
    ctx = context([("Ich habe eine Kette gebaut.", ())], lead=lead, signal=invented)
    points, note = judge(ctx)["anlass_konkret"]

    assert points == 0
    assert "wirklich in der Ausschreibung" in note


def test_a_required_artifact_that_only_restates_the_form_is_not_demanded_again(facts):
    """Phase 7: stage 3 wrote Nordlicht's form into required_artifacts as well.

    ``_mentions`` wants every content word of an entry, so the draft would have had to
    contain "Saetze", "zuletzt", "gebautem" and "automatisiertem" verbatim - five points
    out of reach for a draft that followed the instruction exactly. Measured against the
    real stored fields: 17 of 26 points before, 26 of 26 after.
    """
    lead = make_lead(
        application_format=NORDLICHT_FORM,
        required_artifacts=[NORDLICHT_FORM],
        max_sentences=3,
    )
    ctx = context(
        [("Ich habe mit Claude eine Workbench gebaut.", ("pipeline-latenz",))],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points > 0, note


#: Talwerk's demand (29.07.2026). It stands INSIDE the format sentence, which is why
#: "is it part of the demanded form" was the wrong test for skipping it.
TALWERK_ARTIFACT = "GitHub-Link auf ein Automatisierungs- oder Integrations-Projekt, das du gebaut hast"


def test_a_real_artifact_stated_inside_the_form_sentence_is_still_demanded(facts):
    """The rule above must not swallow a genuine attachment - Talwerk's repository link."""
    lead = make_lead(
        application_format=TALWERK_ARTIFACT + ", und zwei Saetze, warum genau diese Rolle",
        required_artifacts=[TALWERK_ARTIFACT],
        max_sentences=2,
    )
    ctx = context(
        [("Ich habe mit Claude eine Workbench gebaut.", ("pipeline-latenz",))],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points == 0
    assert "GitHub-Link" in note


def test_the_demanded_thing_counts_as_named_when_its_noun_appears(facts):
    """Phase 7: every content word of a prose demand is unsatisfiable.

    The Talwerk answer opens with the repository URL and matches none of the five
    content words of the demand - five points out of reach for the draft that sent
    exactly what was asked for. The noun is what is looked for, split at its parts.
    """
    lead = make_lead(
        application_format=TALWERK_ARTIFACT + ", und zwei Saetze, warum genau diese Rolle",
        required_artifacts=[TALWERK_ARTIFACT],
        max_sentences=2,
    )
    ctx = context(
        [
            (
                "github.com/beispiel/werkbank: meine Workbench, in der Claude "
                "Code taeglich Aufgaben verteilt.",
                ("pipeline-latenz",),
            )
        ],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points > 0, note


def test_a_verb_in_the_demand_does_not_stand_in_for_the_thing(facts):
    """'gebaut' is in Talwerk's demand and in half of all drafts. It is not the artifact."""
    lead = make_lead(
        application_format=TALWERK_ARTIFACT + ", und zwei Saetze, warum genau diese Rolle",
        required_artifacts=[TALWERK_ARTIFACT],
        max_sentences=2,
    )
    ctx = context(
        [("Ich habe eine Kette gebaut und sie laeuft.", ("pipeline-latenz",))],
        lead=lead,
        facts=facts,
    )

    assert judge(ctx)["form_eingehalten"][0] == 0


def test_the_sentence_cap_counts_the_same_sentences_stage_six_counts(facts):
    """Phase 7: a salutation is not one of the three sentences the posting asked for.

    The two measures disagreed until now - stage 6 skipped every uncited paragraph, the
    rubric skipped none - so a draft the generator accepted could lose the points
    anyway. They share one function now.
    """
    from anlass.draft.generate import _count_sentences

    lead = make_lead(max_sentences=3)
    ctx = context(
        [
            ("Hallo Frau Roth,", ()),
            ("Ich habe eine Kette gebaut. Sie laeuft seitdem stabil.", ("pipeline-latenz",)),
            ("Viele Gruesse, Mara Lindqvist", ()),
        ],
        lead=lead,
        facts=facts,
    )
    points, note = judge(ctx)["form_eingehalten"]

    assert points > 0, note
    assert _count_sentences(ctx.draft.paragraphs) == 2


def test_without_any_occasion_the_criterion_asks_for_one():
    built = context([("Ein Text ohne Anlass.", ())])
    ctx = DraftContext(draft=built.draft, lead=built.lead, signal=None, facts=())
    points, note = judge(ctx)["anlass_konkret"]

    assert points == 0
    assert "zitierbaren Anlass" in note


# ------------------------------------------------------------- Q4: evidence spread


def test_a_paragraph_with_five_pieces_of_evidence_is_a_catalogue(facts):
    ctx = context(
        [("Alles auf einmal, in einem einzigen Absatz zusammengetragen.",
          ("cli-crossplatform", "pipeline-latenz", "pipeline-fehler", "tests-abdeckung"))],
        facts=facts,
    )
    points, note = judge(ctx)["belege_verteilt"]

    assert points == 0
    assert "Verteile die Belege" in note
    # Which paragraph, in words rather than by counting: a paragraph number does not
    # survive the rewrite it is asking for (phase 6, finding 3).
    assert "Alles auf einmal" in note


def test_a_long_paragraph_without_evidence_is_flagged(facts):
    ctx = context(
        [(
            "Ich arbeite gerne strukturiert und bringe viel Erfahrung mit, die ich in "
            "verschiedenen Zusammenhaengen einsetzen konnte.",
            (),
        )],
        facts=facts,
    )
    points, note = judge(ctx)["belege_verteilt"]

    assert points == 0
    assert "Ich arbeite gerne strukturiert" in note


# ------------------------------------------------------------ Q5: something shipped


def test_describing_only_the_way_of_working_loses_the_point(facts):
    """Finding Q5: the Nordlicht draft named no built product although it was asked for."""
    ctx = context(
        [("Ich habe die Verarbeitungskette von 7,5 auf 3,8 Sekunden gebracht.",
          ("pipeline-latenz",))],
        facts=facts,
    )
    points, note = judge(ctx)["ausgeliefertes_genannt"]

    assert points == 0
    assert "cli-crossplatform" in note


def test_naming_a_shipped_result_earns_the_point(facts):
    ctx = context(
        [("Das Werkzeug laeuft auf Linux, Windows und macOS.", ("cli-crossplatform",))],
        facts=facts,
    )
    assert judge(ctx)["ausgeliefertes_genannt"][0] > 0


def test_a_fact_base_without_anything_shipped_is_not_punished():
    thin = [Fact(id="arbeitsweise", claim="Ich arbeite in kleinen Schritten.", source="Tagebuch")]
    ctx = context([("Ich arbeite in kleinen Schritten.", ("arbeitsweise",))], facts=thin)
    assert judge(ctx)["ausgeliefertes_genannt"][0] > 0


# ------------------------------------------------- strength against mere eligibility


def test_something_delivered_or_measured_is_a_strength():
    """What :mod:`anlass.signal.detect` reads to rank one occasion above another."""
    delivered = Fact(
        id="werkzeug",
        claim="Das Werkzeug ist ausgeliefert und laeuft auf drei Systemen.",
        source="Fassung 1.4.0",
    )
    measured = Fact(
        id="latenz",
        claim="Zuletzt gemessen: 3,8 Sekunden je Durchlauf.",
        source="Protokoll vom 12.03.2026",
    )
    published_in_the_source_only = Fact(
        id="konto",
        claim="Meine Arbeiten liegen offen.",
        source="github.com/beispiel",
    )
    assert is_strength(delivered)
    assert is_strength(measured)
    assert is_strength(published_in_the_source_only), "die Fundstelle zaehlt mit"


def test_eligibility_is_not_a_strength():
    """Languages, a degree, a place of residence: they answer 'darf', not 'warum'."""
    languages = Fact(
        id="sprachen",
        claim="Deutsch ist meine Erstsprache, Englisch spreche ich verhandlungssicher.",
        source="Zeugnis",
    )
    degree = Fact(
        id="studium",
        claim="Fuer das Wintersemester liegt mir die Zulassung fuer Informatik vor.",
        source="Zulassungsbescheid vom 02.07.2026",
    )
    assert not is_strength(languages)
    assert not is_strength(degree)


def test_a_date_alone_does_not_make_a_fact_a_strength():
    """A number was considered as a marker and measured out again: every eligibility
    fact in the real fact base carries one (a school year, a semester, a phone
    number), so it would have marked exactly the facts this is meant to tell apart."""
    school = Fact(id="abitur", claim="Ich habe im Mai 2026 mein Abitur gemacht.", source="Zeugnis")
    assert not is_strength(school)


# --------------------------------------------------------------- Q6: one thought


def test_a_paragraph_that_jumps_between_topics_is_flagged(facts):
    """Finding Q6: school leaving certificate and languages, then the knowledge store."""
    ctx = context(
        [(
            "Mein Abitur habe ich mit einer Zulassung fuer das Studium abgeschlossen und "
            "spreche drei Sprachen. Der Wissensspeicher durchsucht meine Notizen lokal "
            "in wenigen Millisekunden.",
            ("tests-abdeckung",),
        )],
        facts=facts,
    )
    points, note = judge(ctx)["ein_gedanke_je_absatz"]

    assert points == 0
    assert "Teile Absatz 1" in note


def test_the_note_quotes_where_to_cut_and_offers_the_way_that_adds_no_paragraph(facts):
    """Phase 6, finding 3: this criterion stayed open through every round.

    Two reasons, both in the note: it named a paragraph number and nothing else, so the
    place to cut had to be guessed, and splitting is not always available - stage 6
    refuses a draft that has more paragraphs than the posting allows, so an instruction
    that can only be followed by breaking another rule is not followed at all.
    """
    ctx = context(
        [(
            "Mein Abitur habe ich mit einer Zulassung fuer das Studium abgeschlossen und "
            "spreche drei Sprachen. Der Wissensspeicher durchsucht meine Notizen lokal "
            "in wenigen Millisekunden.",
            ("tests-abdeckung",),
        )],
        facts=facts,
    )
    note = judge(ctx)["ein_gedanke_je_absatz"][1]

    assert "vor dem Satz \"Der Wissensspeicher" in note
    assert "streiche den zweiten Gedanken" in note


def test_two_sentences_about_the_same_thing_are_one_thought(facts):
    ctx = context(
        [(
            "Die Verarbeitungskette lief zuerst in 7,5 Sekunden je Durchlauf. Danach "
            "brauchte dieselbe Verarbeitungskette nur noch 3,8 Sekunden je Durchlauf.",
            ("pipeline-latenz",),
        )],
        facts=facts,
    )
    assert judge(ctx)["ein_gedanke_je_absatz"][0] > 0


# ----------------------------------------------------------- closing, fillers, form


def test_an_ending_without_a_question_or_an_offer_loses_the_point(facts):
    ctx = context([("Das war es von meiner Seite.", ())], facts=facts)
    points, note = judge(ctx)["abschluss_konkret"]

    assert points == 0
    assert "konkreten Frage" in note


def test_application_german_is_named_by_phrase(facts):
    ctx = context(
        [("Hiermit bewerbe ich mich mit grossem Interesse auf Ihre Stelle.", ())],
        facts=facts,
    )
    points, note = judge(ctx)["keine_floskeln"]

    assert points == 0
    assert "hiermit bewerbe ich mich" in note


def test_a_lexicon_entry_matches_a_word_and_not_a_word_inside_a_word(facts):
    """'perfekt' must not fire on 'perfektioniert' - otherwise the lexicon is noise."""
    inside = context([("Den Ablauf habe ich perfektioniert und dabei gemessen.", ())], facts=facts)
    alone = context([("Der Ablauf ist perfekt und laeuft ohne Eingriff.", ())], facts=facts)

    assert judge(inside)["keine_floskeln"][0] > 0
    assert judge(alone)["keine_floskeln"][0] == 0


def test_mixed_spelling_is_caught_and_consistent_spelling_is_not(facts):
    """Finding Q7: 'veroeffentlicht' next to real umlauts in the same letter."""
    mixed = context([("Die Kette läuft, das Ergebnis ist veroeffentlicht.", ())], facts=facts)
    points, note = judge(mixed)["orthographie_einheitlich"]
    assert points == 0
    assert "veroeffentlicht" in note

    consistent = context([("Die Kette laeuft, das Ergebnis ist veroeffentlicht.", ())], facts=facts)
    assert judge(consistent)["orthographie_einheitlich"][0] > 0


def test_length_is_measured_against_the_demanded_sentence_count(facts):
    lead = make_lead(max_sentences=2)
    long_text = "Wort " * 60
    ctx = context([(f"{long_text.strip()}. Und noch ein Satz.", ("pipeline-latenz",))],
                  lead=lead, facts=facts)
    points, note = judge(ctx)["laenge_angemessen"]

    assert points == 0
    assert "Woerter" in note


# ------------------------------------------------------------------------ the file


def write_rubric(tmp_path, text: str):
    path = tmp_path / "rubric.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_can_reweigh_and_switch_off_criteria(tmp_path):
    path = write_rubric(
        tmp_path,
        "max_revisions: 1\ncriteria:\n  form_eingehalten: 10\n  keine_floskeln: 0\n",
    )
    rubric = load_rubric(path)

    assert rubric.max_revisions == 1
    assert rubric.criterion("form_eingehalten").weight == 10
    assert rubric.criterion("keine_floskeln") is None
    assert rubric.possible == default_rubric().possible - 3 + 5


def test_an_unknown_criterion_is_refused_instead_of_ignored(tmp_path):
    """A typo would otherwise switch off a check without anybody noticing."""
    path = write_rubric(tmp_path, "criteria:\n  anrede_passsend: 4\n")
    with pytest.raises(ConfigError) as excinfo:
        load_rubric(path)
    assert "anrede_passsend" in str(excinfo.value)


def test_switching_off_everything_is_refused(tmp_path):
    path = write_rubric(
        tmp_path,
        "criteria:\n" + "".join(f"  {c.name}: 0\n" for c in default_rubric().criteria),
    )
    with pytest.raises(ConfigError):
        load_rubric(path)


def test_own_lexicons_replace_the_shipped_ones(tmp_path, facts):
    path = write_rubric(tmp_path, 'settings:\n  fillers: ["auf jeden fall"]\n')
    rubric = load_rubric(path)

    shipped_filler = context([("Hiermit bewerbe ich mich auf Ihre Stelle.", ())], facts=facts)
    own_filler = context([("Das kriege ich auf jeden Fall hin.", ())], facts=facts)

    assert judge(shipped_filler, rubric)["keine_floskeln"][0] > 0
    assert judge(own_filler, rubric)["keine_floskeln"][0] == 0


def test_a_profile_without_a_rubric_file_uses_the_shipped_one(tmp_path):
    assert rubric_for_profile(tmp_path).possible == default_rubric().possible
    assert rubric_for_profile(None).possible == default_rubric().possible


def test_the_shipped_example_file_loads():
    """The example must stay loadable - it is the documentation of the defaults."""
    from pathlib import Path

    import anlass.critique.rubric as module

    example = Path(module.__file__).with_name("rubric.example.yaml")
    rubric = load_rubric(example)

    assert rubric.possible == default_rubric().possible
    assert rubric.max_revisions == default_rubric().max_revisions
    assert rubric.use_model is False


# ------------------------------------------- what length does not hold against a draft


def test_length_ignores_the_sign_off_and_the_mandated_quote():
    """Length catches padding. Neither of these is a word anyone chose to spend.

    Measured on 2026-07-29 in two separate runs against the Nordlicht posting:
    a three-sentence answer naming three built projects with a measurement each came to
    76 words against a limit of 75, and the words over the line were the name, mail
    address, telephone number and profile link. In the run before it the same criterion
    fought ``anlass_konkret``, which obliges the draft to carry the occasion verbatim -
    fourteen words of a seventy-five word budget, spent before the applicant writes
    anything. The generator resolved that the only way it could, by dropping the quote,
    and lost four points to save two.
    """
    from anlass.models import Draft, FieldValue, Lead, Paragraph, Signal
    from anlass.critique.rubric import DraftContext, _check_length, default_rubric

    quote = "Du hast nachweislich schon etwas mit KI gebaut oder automatisiert."
    body = (
        f"Ihr schreibt: {quote} Genau das mache ich: eine Agenten-Workbench gebaut, "
        "die Aufgaben zuschneidet und an parallel laufende Worker verteilt, dazu ein "
        "Diktierwerkzeug fuer drei Betriebssysteme ausgeliefert."
    )
    sign_off = "Viele Gruesse, Mara Lindqvist, mara@example.org, +49 000 0000000"

    lead = Lead(source="datei", text=f"Wir suchen Verstaerkung. {quote}")
    lead.set("max_sentences", FieldValue(3, provider="extract"))
    signal = Signal(lead_id=lead.id, kind="requirement:f", quote=quote)
    draft = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text=body, fact_ids=("f",)), Paragraph(text=sign_off, fact_ids=())],
    )

    settings = default_rubric().settings
    ctx = DraftContext(draft=draft, lead=lead, signal=signal)
    assert _check_length(ctx, settings).passed, "Grussformel und Pflichtzitat zaehlen mit"

    # And the criterion must still bite when the draft really does pad itself.
    padded = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text=body + " " + ("Fuellwort " * 90), fact_ids=("f",))],
    )
    assert not _check_length(
        DraftContext(draft=padded, lead=lead, signal=signal), settings
    ).passed
