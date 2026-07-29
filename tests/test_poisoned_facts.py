"""The poisoning test - the one the project's argument hangs on.

The fact base does not contain claim X (or contains a different number), the model
claims X anyway, verification must fire. Measured in both directions: a fabrication
that slips through is as bad as a correct sentence that gets rejected.
"""

from __future__ import annotations

import pytest

from anlass.draft.verify import GroundingVerifier
from anlass.llm.fake import FakeLLM
from anlass.models import FindingKind, Severity

from .conftest import CLEAN_PARAGRAPHS, SENDER, make_draft


@pytest.fixture
def verifier() -> GroundingVerifier:
    """Deterministic floor alone - no model involved, so nothing can talk it out."""
    return GroundingVerifier(extra_grounding=(SENDER,))


# --------------------------------------------------------------- must not fire

def test_correct_draft_passes(verifier, lead, signal, facts):
    draft = make_draft(lead, signal, CLEAN_PARAGRAPHS)
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.errors == (), [f.message for f in result.errors]
    assert result.passed is True


@pytest.mark.parametrize("index", range(len(CLEAN_PARAGRAPHS)))
def test_no_clean_paragraph_produces_an_error(verifier, lead, signal, facts, index):
    """False positives measured per paragraph, so a failure names the culprit."""
    draft = make_draft(lead, signal, [CLEAN_PARAGRAPHS[index]])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.errors == (), [f.message for f in result.errors]


def test_number_written_out_counts_as_covered(verifier, lead, signal, facts):
    """'zwei getrennte Busse' is covered by the fact that says 'zwei'."""
    draft = make_draft(
        lead,
        signal,
        [("Der Umbau ging auf zwei getrennte Busse.", ("pipeline-fehler",))],
    )
    assert verifier.verify(draft, facts, lead=lead, signal=signal).passed is True


def test_recipient_data_is_valid_grounding(verifier, lead, signal, facts):
    """Values from the lead carry provenance and are therefore allowed."""
    draft = make_draft(
        lead,
        signal,
        [("Ihre Ausschreibung fuer Werkstudent Datenverarbeitung habe ich gelesen.", ())],
    )
    assert verifier.verify(draft, facts, lead=lead, signal=signal).passed is True


# ------------------------------------------------------------------- must fire

POISON = {
    "erfundene_zahl": (
        "Ich habe die Verarbeitungskette von 7,5 auf 0,9 Sekunden je Durchlauf gebracht.",
        ("pipeline-latenz",),
        FindingKind.UNSUPPORTED_NUMBER,
    ),
    "erfundene_prozentzahl": (
        "Die Laufzeit habe ich um 94 Prozent gesenkt.",
        ("pipeline-latenz",),
        FindingKind.UNSUPPORTED_NUMBER,
    ),
    "erfundener_eigenname_rechtsform": (
        "Gebaut habe ich das bei der Nordwind Datentechnik GmbH.",
        ("pipeline-latenz",),
        FindingKind.UNSUPPORTED_ENTITY,
    ),
    "erfundener_eigenname_produkt": (
        "Die Kette laeuft seither auf einem ESP32 im Dauerbetrieb.",
        ("pipeline-latenz",),
        FindingKind.UNSUPPORTED_ENTITY,
    ),
    "erfundener_ansprechpartner": (
        "Wie mit Herrn Kellermann besprochen, melde ich mich hierzu.",
        (),
        FindingKind.UNSUPPORTED_ENTITY,
    ),
    "erfundene_domain": (
        "Den Quelltext finden Sie unter nordwind-datentechnik.de zum Nachlesen.",
        (),
        FindingKind.UNSUPPORTED_ENTITY,
    ),
    "uebertriebene_steigerung": (
        "Meine Verarbeitungskette ist die schnellste Loesung fuer diesen Zweck.",
        ("pipeline-latenz",),
        FindingKind.UNSUPPORTED_SUPERLATIVE,
    ),
    "erfundenes_zitat": (
        'In Ihrer Ausschreibung steht: "Wir suchen jemanden mit fuenf Jahren Erfahrung".',
        (),
        FindingKind.UNSUPPORTED_QUOTE,
    ),
    "erfundene_kennung": (
        "Meine Arbeit an der Sache ist dokumentiert.",
        ("gibt-es-nicht",),
        FindingKind.UNKNOWN_FACT_ID,
    ),
}


@pytest.mark.parametrize("case", sorted(POISON))
def test_poisoned_paragraph_is_caught(verifier, lead, signal, facts, case):
    text, fact_ids, expected = POISON[case]
    draft = make_draft(lead, signal, [(text, fact_ids)])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is False, f"'{case}' ging durch"
    assert expected in {f.kind for f in result.errors}, [f.message for f in result.errors]


# ----------------------------------------------------------------- invented domains

# The case above uses a single ending, and that is exactly how the gap survived a green
# test once: the floor knew eleven endings by name, the test happened to pick one of
# them, and every other ending walked through. So the domain case runs over a spread of
# endings - including the ones the companies this tool writes to actually use.
INVENTED_DOMAINS = [
    "nordwind-datentechnik.de",
    "nordwind.com",
    "nordwind.io",
    "nordwind.xyz",
    "nordwind.example",
    "nordwind.tech",
    "nordwind.agency",
    "nordwind.digital",
    "nordwind.gmbh",
    "nordwind.consulting",
    "nordwind.systems",
    "nordwind.berlin",
    "post@nordwind.solutions",
    "https://nordwind.ventures/karriere",
]


@pytest.mark.parametrize("domain", INVENTED_DOMAINS)
def test_an_invented_domain_is_caught_whatever_its_ending(verifier, lead, signal, facts, domain):
    draft = make_draft(lead, signal, [(f"Den Quelltext finden Sie unter {domain} zum Nachlesen.", ())])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is False, f"'{domain}' ging durch"
    assert FindingKind.UNSUPPORTED_ENTITY in {f.kind for f in result.errors}


# The other direction: a file name is not a domain. Without this the ending rule would
# trade one hole for a stream of false alarms on ordinary prose.
FILE_NAMES = [
    "Unterlagen.pdf",
    "notizen.txt",
    "aufbau.md",
    "auswertung.py",
    "tests.yml",
    "daten.csv",
    "bild.png",
    "sicherung.zip",
]


@pytest.mark.parametrize("filename", FILE_NAMES)
def test_a_file_name_is_not_reported_as_a_domain(verifier, lead, signal, facts, filename):
    draft = make_draft(lead, signal, [(f"Die Angaben stehen in {filename} bereit.", ())])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.errors == (), [f.message for f in result.errors]


def test_an_upper_case_file_name_is_not_read_as_a_domain(verifier, lead, signal, facts):
    """Measured, not assumed: "Lebenslauf.PDF" trips a finding, but not this rule.

    The suffix list is case-insensitive, so the file name is not taken for a domain.
    What fires is the acronym rule on the token "PDF" - it predates the ending rule and
    applies to every all-caps token. Documented here rather than papered over.
    """
    draft = make_draft(lead, signal, [("Die Angaben stehen in Lebenslauf.PDF bereit.", ())])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert "Lebenslauf.PDF" not in {f.excerpt for f in result.errors}
    assert {f.excerpt for f in result.errors} == {"PDF"}


def test_a_domain_that_stands_in_a_fact_still_passes(verifier, lead, signal, facts):
    """The counter-check to the two above: a covered domain must not fire either."""
    draft = make_draft(
        lead,
        signal,
        [("Das Werkzeug steht unter beispiel.example zum Nachlesen.", ("cli-crossplatform",))],
    )
    assert verifier.verify(draft, facts, lead=lead, signal=signal).passed is True


def test_removed_fact_makes_a_previously_covered_sentence_fail(verifier, lead, signal, facts):
    """The plan's variant: the entry is removed, the text claims it anyway."""
    text, fact_ids = CLEAN_PARAGRAPHS[1]
    draft = make_draft(lead, signal, [(text, ())])
    thinned = [f for f in facts if f.id != "pipeline-latenz"]
    result = verifier.verify(draft, thinned, lead=lead, signal=signal)
    assert result.passed is False
    assert FindingKind.UNSUPPORTED_NUMBER in {f.kind for f in result.errors}


def test_changed_number_in_the_fact_base_is_caught(verifier, lead, signal, facts):
    """Same sentence, a fact base whose number differs by one digit."""
    text, fact_ids = CLEAN_PARAGRAPHS[1]
    draft = make_draft(lead, signal, [(text, fact_ids)])
    altered = [
        f if f.id != "pipeline-latenz" else type(f)(f.id, f.claim.replace("3,8", "4,8"), f.source)
        for f in facts
    ]
    result = verifier.verify(draft, altered, lead=lead, signal=signal)
    assert result.passed is False
    assert "3,8" in {f.excerpt for f in result.errors}


# ---------------------------------------------------- citation versus fabrication

def test_covered_but_uncited_is_a_warning_not_a_block(verifier, lead, signal, facts):
    """The statement is grounded, only the citation is missing. That must not block."""
    text, _ = CLEAN_PARAGRAPHS[1]
    draft = make_draft(lead, signal, [(text, ())])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is True
    assert FindingKind.UNCITED_SUPPORT in {f.kind for f in result.warnings}


def test_long_paragraph_without_any_citation_warns(verifier, lead, signal, facts):
    draft = make_draft(
        lead,
        signal,
        [
            (
                "Ich schreibe Ihnen, weil mich die Aufgabe interessiert und ich mir gut "
                "vorstellen kann, dass wir zusammen etwas aufbauen, das laenger haelt "
                "als ein einzelnes Projekt und Ihnen im Alltag Arbeit abnimmt.",
                (),
            )
        ],
    )
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is True
    assert FindingKind.UNCITED_PARAGRAPH in {f.kind for f in result.warnings}


# ------------------------------------------------------------- the confident liar

def test_model_cannot_clear_a_deterministic_finding(lead, signal, facts):
    """A model that swears everything is fine changes nothing about the floor."""
    liar = FakeLLM(answers=['{"unsupported": []}'])
    verifier = GroundingVerifier(llm=liar, extra_grounding=(SENDER,))
    draft = make_draft(
        lead, signal, [("Die Laufzeit liegt bei 0,4 Sekunden.", ("pipeline-latenz",))]
    )
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is False
    assert liar.calls == 1
    assert FindingKind.UNSUPPORTED_NUMBER in {f.kind for f in result.errors}


def test_model_layer_catches_what_the_floor_cannot(lead, signal, facts):
    """An invented name of ordinary German word shape: known gap, model covers it.

    This is documented rather than papered over: flagging every capitalised German
    word would fire on ordinary prose, because in German every noun is capitalised.
    """
    text = "Die Verarbeitungskette habe ich bei Nordwind verantwortet."
    draft = make_draft(lead, signal, [(text, ("pipeline-latenz",))])

    floor_only = GroundingVerifier(extra_grounding=(SENDER,))
    assert floor_only.verify(draft, facts, lead=lead, signal=signal).passed is True

    model = FakeLLM(
        answers=['{"unsupported": [{"quote": "bei Nordwind", "reason": "Name steht in keinem Beleg"}]}']
    )
    with_model = GroundingVerifier(llm=model, extra_grounding=(SENDER,))
    result = with_model.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is False
    flagged = [f for f in result.errors if f.kind is FindingKind.MODEL_FLAGGED]
    assert flagged and flagged[0].paragraph == 0


def test_model_outage_is_a_warning_and_the_floor_still_decides(lead, signal, facts):
    broken = FakeLLM(fail_with="Ollama ist nicht erreichbar")
    verifier = GroundingVerifier(llm=broken, extra_grounding=(SENDER,))
    draft = make_draft(lead, signal, CLEAN_PARAGRAPHS)
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is True
    assert FindingKind.MODEL_UNAVAILABLE in {f.kind for f in result.warnings}
    assert all(f.severity is Severity.WARNING for f in result.findings)


def test_unparsable_model_answer_does_not_block(lead, signal, facts):
    verifier = GroundingVerifier(llm=FakeLLM(answers=["Klingt gut, keine Auffaelligkeiten."]))
    draft = make_draft(lead, signal, [("Viele Gruesse", ())])
    result = verifier.verify(draft, facts, lead=lead, signal=signal)
    assert result.passed is True
    assert FindingKind.MODEL_UNAVAILABLE in {f.kind for f in result.warnings}


# ------------------------------------------------- bulk fields must not ground anything


def test_a_long_field_does_not_ground_the_way_the_raw_text_does_not():
    """A scraped page in a field must not become a licence to claim things.

    Measured on 2026-07-29 against the real run: stage 3 put an 18k-character job-board
    page into ``page_text``, complete with cookie notice, navigation and dozens of
    unrelated company names. Every field value counted as grounding, so a fabricated
    former employer lifted out of that soup was refused without the lead and accepted
    with it - defeating the very reason ``include_lead_text`` defaults to off.
    """
    from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph
    from anlass.draft.verify import GroundingVerifier

    facts = [Fact(id="q", claim="Quassel laeuft auf Linux.", source="repo")]
    invented = "SpiraTec"
    haystack = ("Datenschutz Anmelden Impressum " * 400) + invented
    assert len(haystack) > 400

    lead = Lead(source="datei", text="")
    lead.set("organization", FieldValue("Beispiel GmbH", provider="datei"))
    lead.set("page_text", FieldValue(haystack, provider="page"))

    draft = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text=f"Zuvor war ich bei {invented} zustaendig.", fact_ids=("q",))],
    )
    findings = GroundingVerifier().verify(draft, facts, lead=lead).findings
    errors = [f for f in findings if f.severity.value == "error"]
    assert errors, "ein erfundener Name aus einem langen Feld wurde durchgelassen"


def test_short_recipient_fields_still_ground():
    """The fix must not cost the legitimate case: short identifying fields still count."""
    from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph
    from anlass.draft.verify import GroundingVerifier

    facts = [Fact(id="q", claim="Quassel laeuft auf Linux.", source="repo")]
    lead = Lead(source="datei", text="")
    lead.set("organization", FieldValue("Nordlicht Systeme GmbH", provider="datei"))
    lead.set("location", FieldValue("Beispielstadt", provider="datei"))

    draft = Draft(
        lead_id=lead.id,
        paragraphs=[
            Paragraph(
                text="Ihre Ausschreibung bei Nordlicht Systeme GmbH in Beispielstadt.",
                fact_ids=("q",),
            )
        ],
    )
    findings = GroundingVerifier().verify(draft, facts, lead=lead).findings
    assert not [f for f in findings if f.severity.value == "error"]


# ------------------------------------------- spelled-out numbers are checked, not just grounded


def test_a_fabricated_number_written_as_a_word_is_caught():
    """The shape a lie in an application takes: "zehn Jahre Berufserfahrung bei Google".

    Detection and grounding used to part ways here. The grounding side went through
    ``_numbers_in``, which knows the number words; the check loop matched ``_NUMBER_RE``,
    which only ever sees digits. So a fabricated quantity written as a word was never
    checked at all. Found on 2026-07-29 with a poisoned posting that instructed the model
    to claim exactly that sentence - the model ignored the instruction, but a draft
    carrying the claim passed the verifier with no finding.
    """
    from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph
    from anlass.draft.verify import GroundingVerifier

    facts = [Fact(id="p", claim="Ich programmiere in Python.", source="repo")]
    lead = Lead(source="datei", text="Wir suchen Verstaerkung.")
    lead.set("organization", FieldValue("Beispiel GmbH", provider="datei"))

    for sentence in (
        "Ich bringe zehn Jahre Berufserfahrung bei Google mit.",
        "Ich habe drei Doktortitel in Informatik.",
        "Wir haben acht Systeme parallel betrieben.",
    ):
        draft = Draft(lead_id=lead.id, paragraphs=[Paragraph(text=sentence, fact_ids=("p",))])
        findings = GroundingVerifier().verify(draft, facts, lead=lead).findings
        assert [f for f in findings if f.severity.value == "error"], sentence


def test_a_spelled_out_number_that_is_grounded_still_passes():
    """The fix must not cost the honest case."""
    from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph
    from anlass.draft.verify import GroundingVerifier

    facts = [
        Fact(
            id="messung",
            claim="Zuletzt gemessen: 3,8 Sekunden je Durchlauf auf zwei Rechnern.",
            source="Messung",
        )
    ]
    lead = Lead(source="datei", text="Wir suchen Verstaerkung.")
    lead.set("organization", FieldValue("Beispiel GmbH", provider="datei"))

    draft = Draft(
        lead_id=lead.id,
        paragraphs=[Paragraph(text="Gemessen auf zwei Rechnern, 3,8 Sekunden.", fact_ids=("messung",))],
    )
    findings = GroundingVerifier().verify(draft, facts, lead=lead).findings
    assert not [f for f in findings if f.severity.value == "error"]
