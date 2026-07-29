"""Generation: the model sees the facts and nothing else, and must cite per paragraph."""

from __future__ import annotations

import json

import pytest

from anlass.draft.generate import FactGroundedDrafter, build_prompt
from anlass.errors import DraftError
from anlass.llm.fake import FakeLLM
from anlass.models import Fact, Field, FieldValue, Lead

from .test_enrich_extract import TALWERK_TEXT, NORDLICHT_TEXT

ANSWER = json.dumps(
    {
        "subject": "Ihre Ausschreibung",
        "paragraphs": [
            {"text": "Sehr geehrte Frau Roth,", "fact_ids": []},
            {
                "text": "Ich habe eine Verarbeitungskette von 7,5 auf 3,8 Sekunden gebracht.",
                "fact_ids": ["pipeline-latenz"],
            },
        ],
    }
)


def test_draft_carries_the_cited_ids_per_paragraph(lead, signal, facts):
    drafter = FactGroundedDrafter(llm=FakeLLM(answers=[ANSWER], label="lokal"))
    draft = drafter.draft(lead, signal, facts, voice="Sachlich, knapp.")
    assert draft.lead_id == lead.id
    assert draft.signal_id == signal.id
    assert draft.subject == "Ihre Ausschreibung"
    assert draft.model == "fake:lokal"
    assert draft.paragraphs[0].fact_ids == ()
    assert draft.paragraphs[1].fact_ids == ("pipeline-latenz",)


def test_answer_wrapped_in_a_code_fence_is_accepted(lead, signal, facts):
    llm = FakeLLM(answers=[f"Gerne:\n```json\n{ANSWER}\n```\n"])
    draft = FactGroundedDrafter(llm=llm).draft(lead, signal, facts)
    assert len(draft.paragraphs) == 2


def test_the_model_only_sees_the_facts_it_was_given(lead, signal, facts):
    llm = FakeLLM(answers=[ANSWER])
    given = [f for f in facts if f.id != "tests-abdeckung"]
    withheld = next(f for f in facts if f.id == "tests-abdeckung")
    FactGroundedDrafter(llm=llm).draft(lead, signal, given, voice="Sachlich.")
    prompt = llm.prompts[0]
    assert "pipeline-latenz" in prompt
    assert withheld.claim not in prompt
    assert withheld.id not in prompt
    assert signal.quote in prompt
    assert "Sachlich." in prompt


def test_prompt_names_the_recipient_fields_with_their_provenance(lead, signal, facts):
    prompt = build_prompt(lead, signal, facts, voice="")
    assert "organization: Nordlicht Systeme (Quelle: datei)" in prompt
    assert "remote: True (Quelle: extraktion)" in prompt


def test_prompt_without_a_signal_forbids_inventing_one(lead, facts):
    prompt = build_prompt(lead, None, facts)
    assert "Behaupte keinen" in prompt


def test_citation_of_an_unknown_id_is_refused(lead, signal, facts):
    answer = json.dumps({"paragraphs": [{"text": "Text", "fact_ids": ["gibt-es-nicht"]}]})
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "gibt-es-nicht" in str(excinfo.value)


@pytest.mark.parametrize(
    "answer",
    [
        "Kein JSON hier.",
        json.dumps({"paragraphs": []}),
        json.dumps({"paragraphs": [{"text": "  ", "fact_ids": []}]}),
        json.dumps({"paragraphs": [{"text": "x", "fact_ids": "keine-liste-sondern-string"}]}),
        json.dumps({"paragraphs": ["nur ein String"]}),
        json.dumps({"absaetze": []}),
    ],
)
def test_unusable_answers_are_refused(lead, signal, facts, answer):
    with pytest.raises(DraftError):
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)


def test_a_single_id_as_string_is_accepted(lead, signal, facts):
    answer = json.dumps({"paragraphs": [{"text": "Text", "fact_ids": "pipeline-latenz"}]})
    draft = FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert draft.paragraphs[0].fact_ids == ("pipeline-latenz",)


def test_too_many_paragraphs_are_refused(lead, signal, facts):
    answer = json.dumps({"paragraphs": [{"text": f"Absatz {i}", "fact_ids": []} for i in range(5)]})
    with pytest.raises(DraftError):
        FactGroundedDrafter(llm=FakeLLM(answers=[answer]), max_paragraphs=4).draft(lead, signal, facts)


def test_no_facts_no_draft(lead, signal):
    with pytest.raises(DraftError):
        FactGroundedDrafter(llm=FakeLLM(answers=[ANSWER])).draft(lead, signal, [])


def test_duplicate_ids_in_the_fact_base_are_refused(lead, signal):
    facts = [Fact("doppelt", "a", "b"), Fact("doppelt", "c", "d")]
    with pytest.raises(ValueError):
        FactGroundedDrafter(llm=FakeLLM(answers=[ANSWER])).draft(lead, signal, facts)


# Phase 5b: the posting's own application instructions, passed to the model as a
# fact, and the subset of them (Q1's sentence cap, Q2's address form) that this
# stage can also check mechanically rather than trust the model to have followed.


def test_revision_notes_reach_the_model_as_a_binding_block(lead, signal, facts):
    llm = FakeLLM(answers=[ANSWER])
    FactGroundedDrafter(llm=llm).draft(
        lead, signal, facts, revision_notes=["Schreibe die Anrede in Du-Form."]
    )
    assert "UEBERARBEITUNG" in llm.prompts[0]
    assert "Schreibe die Anrede in Du-Form." in llm.prompts[0]


def test_no_revision_notes_means_no_revision_block(lead, signal, facts):
    prompt = build_prompt(lead, signal, facts)
    assert "UEBERARBEITUNG" not in prompt


def test_no_application_fields_means_no_bewerbungsvorgabe_block(signal, facts):
    lead = Lead(source="test", text="")
    prompt = build_prompt(lead, signal, facts)
    assert "BEWERBUNGSVORGABE (Tatsache aus der Ausschreibung, keine Bitte)" not in prompt


def test_application_instructions_reach_the_prompt_as_fact_not_wish(lead, signal, facts):
    """Phase 7: the countable rules moved out of this block and lead the prompt.

    The two assertions on "Hoechstens so viele Saetze insgesamt: 2" and "Anrede: sie"
    are replaced by the ones below, not dropped: the same two facts still have to reach
    the model, they now stand in the HARTE GRENZEN block in front of everything else
    (measured: 2 of 8 answers kept the cap from the application block, 4 of 8 from the
    front). What is not countable stays here.
    """
    lead.set(Field.FORBIDDEN_ARTIFACTS, FieldValue(["Anschreiben"], provider="extract:fake"))
    lead.set(Field.MAX_SENTENCES, FieldValue(2, provider="extract:fake"))
    lead.set(Field.FORM_OF_ADDRESS, FieldValue("sie", provider="extract:fake"))
    prompt = build_prompt(lead, signal, facts)
    assert "BEWERBUNGSVORGABE (Tatsache aus der Ausschreibung, keine Bitte)" in prompt
    assert "Nicht gewuenscht - erzeuge nichts davon: Anschreiben" in prompt
    assert prompt.startswith("HARTE GRENZEN")
    assert "Hoechstens 2 Saetze" in prompt
    assert "Anrede: Sie-Form" in prompt


def test_a_formal_opening_is_refused_when_the_posting_duzt(lead, signal, facts):
    lead.set(Field.FORM_OF_ADDRESS, FieldValue("du", provider="extract:fake"))
    answer = json.dumps({"paragraphs": [{"text": "Sehr geehrte Damen und Herren,", "fact_ids": []}]})
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "duzt" in str(excinfo.value)


def test_a_du_pronoun_is_refused_when_the_posting_siezt(lead, signal, facts):
    lead.set(Field.FORM_OF_ADDRESS, FieldValue("sie", provider="extract:fake"))
    answer = json.dumps(
        {"paragraphs": [{"text": "Du hast eine spannende Ausschreibung veroeffentlicht.", "fact_ids": []}]}
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "siezt" in str(excinfo.value)


def test_exceeding_max_sentences_in_one_entry_is_refused(lead, signal, facts):
    """Phase 8 replaces the paragraph version of this test, it does not drop it.

    The rule is the same rule - more sentences than the posting allows - but under a
    sentence bound the answer comes back as a list, so the case is now two sentences in
    one entry. The message says which entry and how many, because "halte die Grenze
    ein" was the note that went unheeded five rounds running (Q19).
    """
    lead.set(Field.MAX_SENTENCES, FieldValue(1, provider="extract:fake"))
    answer = json.dumps(
        {
            "sentences": [
                {
                    "text": "Ich habe die Kette beschleunigt. Danach lief sie stabil.",
                    "fact_ids": ["pipeline-latenz"],
                }
            ]
        }
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "Eintrag 1 enthaelt 2 Saetze" in str(excinfo.value)
    assert excinfo.value.violation == "satz-je-eintrag"


def test_max_sentences_ignores_the_greeting_and_signoff(lead, signal, facts):
    """Same guarantee as before, now carried by the shape instead of by markers.

    Salutation and sign-off have their own keys and are not entries, so the model no
    longer has to know which paragraphs count towards the bound - it cannot put them in
    the list at all.
    """
    lead.set(Field.MAX_SENTENCES, FieldValue(1, provider="extract:fake"))
    answer = json.dumps(
        {
            "salutation": "Sehr geehrte Frau Roth,",
            "sentences": [
                {"text": "Ich habe die Kette beschleunigt.", "fact_ids": ["pipeline-latenz"]}
            ],
            "closing": "Viele Gruesse, Mara",
        }
    )
    draft = FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert [p.text for p in draft.paragraphs] == [
        "Sehr geehrte Frau Roth,",
        "Ich habe die Kette beschleunigt.",
        "Viele Gruesse, Mara",
    ]
    assert draft.paragraphs[1].fact_ids == ("pipeline-latenz",)


def test_more_than_three_facts_in_one_paragraph_is_refused(lead, signal, facts):
    answer = json.dumps(
        {
            "paragraphs": [
                {
                    "text": "Vier Belege in einem Satz.",
                    "fact_ids": ["cli-crossplatform", "pipeline-latenz", "pipeline-fehler", "tests-abdeckung"],
                }
            ]
        }
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "hoechstens 3" in str(excinfo.value)


def test_nordlichts_real_constraints_reach_the_prompt_and_are_enforced(lead, signal, facts):
    """Nordlicht text (see tests/test_enrich_extract.py): 'in 3 Saetzen' and a
    text that duzt throughout. Simulates what extract.py would have written onto the
    lead, then checks generate.py both states it as fact and enforces the two parts
    of it that are mechanically checkable."""
    lead.text = NORDLICHT_TEXT
    lead.set(Field.MAX_SENTENCES, FieldValue(3, provider="extract:fake"))
    lead.set(Field.FORM_OF_ADDRESS, FieldValue("du", provider="extract:fake"))
    prompt = build_prompt(lead, signal, facts)
    # Phase 7: both stand in the HARTE GRENZEN block now, see the test above.
    # Phase 8: the bound no longer asks for a count, it names the shape that carries it.
    assert "Hoechstens 3 Saetze" in prompt
    assert "Liste aus genau 3 Eintraegen" in prompt
    assert "Anrede: Du-Form" in prompt

    too_long = json.dumps(
        {
            "sentences": [
                {"text": "Ich habe eine Kette gebaut.", "fact_ids": ["pipeline-latenz"]},
                {"text": "Sie lief stabil.", "fact_ids": []},
                {"text": "Ein Fehler kam vor.", "fact_ids": []},
                {"text": "Er wurde behoben.", "fact_ids": []},
            ]
        }
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[too_long])).draft(lead, signal, facts)
    assert "4 Eintraege geliefert, verlangt sind genau 3" in str(excinfo.value)

    wrong_form = json.dumps(
        {
            "salutation": "Sehr geehrte Damen und Herren,",
            "sentences": [
                {"text": "Ich habe eine Kette gebaut.", "fact_ids": ["pipeline-latenz"]},
                {"text": "Sie lief stabil.", "fact_ids": []},
                {"text": "Ein Fehler wurde behoben.", "fact_ids": []},
            ],
        }
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[wrong_form])).draft(lead, signal, facts)
    assert "duzt" in str(excinfo.value)


def _three_entries() -> list[dict]:
    return [
        {"text": "Ich habe eine Kette gebaut.", "fact_ids": ["pipeline-latenz"]},
        {"text": "Sie lief stabil.", "fact_ids": []},
        {"text": "Ein Fehler wurde behoben.", "fact_ids": []},
    ]


@pytest.mark.parametrize(
    "answer, expected, violation",
    [
        pytest.param(
            {"sentences": _three_entries() + [{"text": "Und noch einer.", "fact_ids": []}]},
            "4 Eintraege geliefert, verlangt sind genau 3",
            "eintragsanzahl",
            id="zu-viele-eintraege",
        ),
        pytest.param(
            {"sentences": _three_entries()[:2]},
            "2 Eintraege geliefert, verlangt sind genau 3",
            "eintragsanzahl",
            id="zu-wenige-eintraege",
        ),
        pytest.param(
            {
                "sentences": [
                    {"text": "Eins. Zwei.", "fact_ids": ["pipeline-latenz"]},
                    {"text": "Drei.", "fact_ids": []},
                    {"text": "Vier.", "fact_ids": []},
                ]
            },
            "Eintrag 1 enthaelt 2 Saetze",
            "satz-je-eintrag",
            id="zwei-saetze-in-einem-eintrag",
        ),
        pytest.param(
            {"salutation": "Sehr geehrte Damen und Herren,", "sentences": _three_entries()},
            "duzt",
            "anrede",
            id="anrede",
        ),
        pytest.param(
            {
                "sentences": [
                    {
                        "text": "Vier Belege.",
                        "fact_ids": [
                            "cli-crossplatform",
                            "pipeline-latenz",
                            "pipeline-fehler",
                            "tests-abdeckung",
                        ],
                    },
                    {"text": "Sie lief stabil.", "fact_ids": []},
                    {"text": "Ein Fehler wurde behoben.", "fact_ids": []},
                ]
            },
            "4 Belege",
            "belege",
            id="belege",
        ),
        pytest.param(
            {"paragraphs": [{"text": "Eins. Zwei. Drei.", "fact_ids": ["pipeline-latenz"]}]},
            "in Absaetzen statt mit der verlangten Liste",
            "listenform",
            id="falsche-form",
        ),
    ],
)
def test_a_refusal_carries_the_refused_text(lead, signal, facts, answer, expected, violation):
    """Phase 7: a countable violation makes the attempt wrong, not worthless.

    Four rounds against the Nordlicht posting all broke the sentence cap on
    29.07.2026 and the run ended with nothing to show. The text existed every time. It
    now leaves this stage with the refusal, so the loop can keep it and hand the best
    one over with the violation named.

    Phase 8 rewrites the cases, not the property: under a sentence bound the answer is a
    list, so the countable rules are about entries. The last case is the one phase 8
    added - the model answered in the old shape - and it is here for the same reason as
    the rest: the prose may be perfectly good, and it is not thrown away for arriving in
    the wrong container.
    """
    lead.set(Field.MAX_SENTENCES, FieldValue(3, provider="extract:fake"))
    lead.set(Field.FORM_OF_ADDRESS, FieldValue("du", provider="extract:fake"))
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[json.dumps(answer)])).draft(lead, signal, facts)

    assert expected in str(excinfo.value)
    assert excinfo.value.violation == violation
    assert excinfo.value.draft is not None
    assert excinfo.value.draft.paragraphs, "der zurueckgewiesene Text steht vollstaendig drin"


def test_a_refused_paragraph_count_carries_its_text_too(lead, signal, facts):
    """The same property for the free shape, which is where a paragraph bound applies.

    Without a sentence bound nothing about the answer's shape is fixed, so the number of
    paragraphs is still this stage's own limit and still refused - with the text.
    """
    answer = json.dumps({"paragraphs": [{"text": f"Absatz {i}.", "fact_ids": []} for i in range(5)]})
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert "5 Absaetze" in str(excinfo.value)
    assert excinfo.value.violation == "absatzanzahl"
    assert excinfo.value.draft is not None


def test_an_unusable_answer_carries_no_text(lead, signal, facts):
    """The other case, and it stays the other case: nothing was produced, nothing kept."""
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=["kein JSON"])).draft(lead, signal, facts)
    assert excinfo.value.draft is None


def test_talwerks_forbidden_artifact_reaches_the_prompt_as_a_binding_fact(lead, signal, facts):
    """Talwerk text ends in 'Keine Anschreiben' - Q1's exact case. Simulates the
    extracted field landing on the lead and checks it reaches generate.py's prompt
    as the BEWERBUNGSVORGABE fact, worded as binding, not as a suggestion."""
    lead.text = TALWERK_TEXT
    lead.set(Field.FORBIDDEN_ARTIFACTS, FieldValue(["Anschreiben"], provider="extract:fake"))
    lead.set(Field.REQUIRED_ARTIFACTS, FieldValue(["GitHub-Link"], provider="extract:fake"))
    prompt = build_prompt(lead, signal, facts)
    assert "Nicht gewuenscht - erzeuge nichts davon: Anschreiben" in prompt
    assert "Verlangt: GitHub-Link" in prompt


# Phase 8: where the posting bounds the number of sentences, the answer's shape carries
# the number instead of the model counting its own full stops. Measured over four fresh
# runs on 29.07.2026, five of six rounds against the Talwerk posting broke the same
# two-sentence bound after being told about it five times.


def test_a_sentence_bound_switches_the_answer_to_a_list_of_that_many_entries(lead, signal, facts):
    lead.set(Field.MAX_SENTENCES, FieldValue(2, provider="extract:fake"))
    prompt = build_prompt(lead, signal, facts)
    assert '"sentences" enthaelt GENAU 2 Eintraege' in prompt
    assert "genau ein Satz mit genau einem Satzzeichen am Ende" in prompt
    assert '"paragraphs"' not in prompt


def test_without_a_sentence_bound_the_answer_stays_free_prose(lead, signal, facts):
    """A letter is prose, and nothing about phase 8 changes that."""
    prompt = build_prompt(lead, signal, facts)
    assert '"paragraphs"' in prompt
    assert '"sentences"' not in prompt
    assert "Hoechstens 4 Absaetze" in prompt


def test_exactly_as_many_entries_as_sentences_allowed_is_accepted(lead, signal, facts):
    lead.set(Field.MAX_SENTENCES, FieldValue(2, provider="extract:fake"))
    answer = json.dumps(
        {
            "subject": "Ihre Ausschreibung",
            "salutation": "Hallo Frau Roth,",
            "sentences": [
                {"text": "Ich habe die Kette beschleunigt.", "fact_ids": ["pipeline-latenz"]},
                {"text": "Die Testsuite deckt sie ab.", "fact_ids": ["tests-abdeckung"]},
            ],
            "closing": "Viele Gruesse, Mara",
        }
    )
    draft = FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert len(draft.paragraphs) == 4, "Anrede, zwei Saetze, Gruss"
    assert draft.subject == "Ihre Ausschreibung"
    assert [p.fact_ids for p in draft.paragraphs] == [
        (),
        ("pipeline-latenz",),
        ("tests-abdeckung",),
        (),
    ]


def test_a_bounded_answer_may_leave_out_greeting_and_signoff(lead, signal, facts):
    """Talwerk's case: 'Keine Anschreiben' means the frame stays empty, not that the
    answer is missing something. Both keys are optional and their absence is silent."""
    lead.set(Field.MAX_SENTENCES, FieldValue(1, provider="extract:fake"))
    answer = json.dumps(
        {"sentences": [{"text": "Ich habe die Kette beschleunigt.", "fact_ids": ["pipeline-latenz"]}]}
    )
    draft = FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert [p.text for p in draft.paragraphs] == ["Ich habe die Kette beschleunigt."]


@pytest.mark.parametrize(
    "frame, expected",
    [
        pytest.param(
            {"salutation": "Werte Damen und Herren,"}, "als Anrede nicht zu erkennen", id="anrede"
        ),
        pytest.param({"closing": "Bis dann"}, "als Grussformel nicht zu erkennen", id="gruss"),
    ],
)
def test_a_frame_that_is_not_recognisable_as_one_is_refused(lead, signal, facts, frame, expected):
    """Otherwise the two measures drift apart, which is this rubric's oldest defect.

    Salutation and sign-off do not count towards the bound - but the rubric decides
    which paragraphs those are from their opening words, not from the key they arrived
    under. A greeting it cannot recognise would be counted as a body sentence there
    while this stage counted it as frame, and a draft that kept the bound would lose
    points for keeping it (Q12, Q15, Q17: one measure disagreeing with another). Refused
    here, with the way out named, so both stages count the same text the same way.
    """
    lead.set(Field.MAX_SENTENCES, FieldValue(1, provider="extract:fake"))
    answer = json.dumps(
        {
            "sentences": [
                {"text": "Ich habe die Kette beschleunigt.", "fact_ids": ["pipeline-latenz"]}
            ],
            **frame,
        }
    )
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[answer])).draft(lead, signal, facts)
    assert expected in str(excinfo.value)
    assert excinfo.value.violation == "rahmen"
    assert excinfo.value.draft is not None


def test_a_bounded_answer_with_no_list_at_all_carries_no_text(lead, signal, facts):
    """The other case stays the other case: nothing usable came back, nothing is kept."""
    lead.set(Field.MAX_SENTENCES, FieldValue(2, provider="extract:fake"))
    with pytest.raises(DraftError) as excinfo:
        FactGroundedDrafter(llm=FakeLLM(answers=[json.dumps({"subject": "x"})])).draft(
            lead, signal, facts
        )
    assert excinfo.value.draft is None


def test_a_bounded_answer_may_have_more_paragraphs_than_the_free_shape_allows(lead, signal, facts):
    """Five paragraphs where four are allowed - and correct, because the posting asked.

    Three demanded sentences plus a greeting and a sign-off are five paragraphs. The
    paragraph bound is this stage's own rule for free prose; applying it to a shape the
    posting dictated would refuse a draft for following the instruction.
    """
    lead.set(Field.MAX_SENTENCES, FieldValue(3, provider="extract:fake"))
    answer = json.dumps(
        {
            "salutation": "Hallo Frau Roth,",
            "sentences": [
                {"text": "Ich habe die Kette beschleunigt.", "fact_ids": ["pipeline-latenz"]},
                {"text": "Die Testsuite deckt sie ab.", "fact_ids": ["tests-abdeckung"]},
                {"text": "Der Fehler ist behoben.", "fact_ids": ["pipeline-fehler"]},
            ],
            "closing": "Viele Gruesse, Mara",
        }
    )
    draft = FactGroundedDrafter(llm=FakeLLM(answers=[answer]), max_paragraphs=4).draft(
        lead, signal, facts
    )
    assert len(draft.paragraphs) == 5
