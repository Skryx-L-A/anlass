"""Stage 5: the quotable occasion, and that a quote is never invented."""

from __future__ import annotations

from anlass.interfaces import SignalDetector
from anlass.models import Fact, Field, FieldValue, Lead
from anlass.signal.detect import CompositeSignalDetector, JobRequirementSignalDetector

FACTS = [
    Fact(
        id="cli-crossplatform",
        claim="Das Werkzeug laeuft auf Linux, Windows und macOS.",
        source="beispiel.example/werkzeug",
    ),
    Fact(
        id="pipeline-latenz",
        claim="Die Verarbeitungskette wurde von 7,5 auf 3,8 Sekunden je Durchlauf gesenkt.",
        source="Messprotokoll",
    ),
]


def _lead(text: str, url: str | None = None) -> Lead:
    entry = Lead(source="datei", text=text)
    if url:
        entry.set(Field.URL, FieldValue(url, provider="datei"))
    return entry


def test_conforms_to_the_signal_detector_protocol():
    assert isinstance(JobRequirementSignalDetector(FACTS), SignalDetector)


def test_quote_is_verbatim_source_text_at_its_own_offsets():
    lead = _lead(
        "Wir suchen jemanden mit Erfahrung auf Linux, Windows und macOS. "
        "Ausserdem: ein Buero mit Blick auf den Hafen."
    )
    signals = JobRequirementSignalDetector(FACTS).detect(lead)
    assert signals
    top = signals[0]
    assert lead.text[top.start : top.end] == top.quote
    assert top.quote in lead.text
    assert top.lead_id == lead.id
    assert top.detector == "job_requirement"


def test_kind_names_the_backing_fact():
    lead = _lead("Wir setzen auf Linux, Windows und macOS gleichermassen.")
    signals = JobRequirementSignalDetector(FACTS).detect(lead)
    assert signals[0].kind == "requirement:cli-crossplatform"


def test_no_overlap_returns_no_signal_not_a_guess():
    lead = _lead("Wir suchen eine Reinigungskraft fuer unser Buero in Kiel.")
    assert JobRequirementSignalDetector(FACTS).detect(lead) == []


def test_empty_text_or_empty_fact_base_returns_no_signal():
    assert JobRequirementSignalDetector(FACTS).detect(_lead("   ")) == []
    assert JobRequirementSignalDetector([]).detect(_lead("Linux, Windows, macOS.")) == []


def test_strongest_overlap_sorts_first():
    lead = _lead(
        "Erfahrung mit Linux ist vorhanden. "
        "Die Verarbeitungskette wurde von 7,5 auf 3,8 Sekunden je Durchlauf gesenkt, das ist belegt."
    )
    signals = JobRequirementSignalDetector(FACTS).detect(lead)
    assert len(signals) == 2
    # second sentence shares five keywords with its fact, the first only one - must sort first
    assert signals[0].kind == "requirement:pipeline-latenz"


def test_a_signal_without_a_quote_cannot_be_constructed():
    # anlass.models.Signal enforces this itself; a detector inherits the guarantee.
    import pytest

    from anlass.models import Signal

    with pytest.raises(ValueError):
        Signal(lead_id="l1", kind="x", quote="   ")


def test_composite_detector_concatenates_in_detector_order():
    class Stub:
        def __init__(self, name: str, quote: str) -> None:
            self._name = name
            self._quote = quote

        @property
        def name(self) -> str:
            return self._name

        def detect(self, lead: Lead) -> list:
            from anlass.models import Signal

            return [Signal(lead_id=lead.id, kind=self._name, quote=self._quote, detector=self._name)]

    lead = _lead("beliebiger Text")
    first = Stub("erster", "irrelevanter Text A")
    second = Stub("zweiter", "irrelevanter Text B")
    composite = CompositeSignalDetector([first, second], name="mein-mix")
    signals = composite.detect(lead)
    assert [s.kind for s in signals] == ["erster", "zweiter"]
    assert composite.name == "mein-mix"


# ---------------------------------------------------------------------------------
# Phase 5c: length-normalised, rarity-weighted ranking (fixes the Q3 finding: a long,
# generically worded fact outranking a short, sharply relevant one purely because it
# has more keywords to overlap with).
# ---------------------------------------------------------------------------------


def test_requirement_sentence_is_preferred_over_a_stronger_plain_sentence():
    # Rigged so the plain sentence has the higher *raw* weighted-Jaccard score (0.330
    # vs 0.321) - without the boost it would win. Only the x1.3 requirement boost
    # (task point 5) flips the order, so this actually exercises the boost rather
    # than a coincidence of the numbers.
    team_fact = Fact(
        id="team-projekte",
        claim=(
            "Wir realisieren gemeinsam originelle Projekte im Team, feiern jeden "
            "Erfolg und dokumentieren die Ergebnisse ausfuehrlich fuer alle "
            "Beteiligten."
        ),
        source="Erfundenes Beispiel",
    )
    aufgaben_fact = Fact(
        id="selbststaendige-aufgaben",
        claim="Ich arbeite selbststaendig an anspruchsvollen technischen Aufgaben.",
        source="Erfundenes Beispiel",
    )
    lead = _lead(
        "Wir realisieren gemeinsam originelle Projekte im groesseren Team. "
        "Voraussetzung ist, dass du selbststaendig an technischen Aufgaben arbeitest."
    )
    signals = JobRequirementSignalDetector([team_fact, aufgaben_fact]).detect(lead)
    assert signals[0].kind == "requirement:selbststaendige-aufgaben"


def test_a_match_below_minimum_similarity_is_discarded_as_coincidence():
    # "Team" and "Projekt" are the only words this fact shares with the lead text,
    # each buried in a much longer sentence on both sides - a coincidence, not an
    # occasion (task point 4).
    weak_fact = Fact(
        id="unrelated-weak",
        claim=(
            "Ich habe auch schon in einem groesseren Team an einem Projekt "
            "gearbeitet, dabei viel ueber Zusammenarbeit, Planung und "
            "Kommunikation gelernt und regelmaessig Feedback eingeholt."
        ),
        source="Erfundenes Beispiel",
    )
    lead = _lead(
        "Wir sind ein wachsendes Unternehmen und suchen dringend Verstaerkung "
        "fuer unser hochmotiviertes Team. Bewirb dich jetzt auf unser aktuelles "
        "Projekt im Bereich Kundenservice und Support."
    )
    assert JobRequirementSignalDetector([weak_fact]).detect(lead) == []
    # The same pair does match once the bar is lowered - proves it is the threshold
    # rejecting it, not a zero-overlap case already covered elsewhere.
    permissive = JobRequirementSignalDetector([weak_fact], min_similarity=0.0)
    assert permissive.detect(lead) != []


def test_default_returns_at_most_three_signals_one_per_fact():
    facts = [
        Fact(id=f"fact-{letter}", claim=claim, source="Erfundenes Beispiel")
        for letter, claim in [
            ("a", "Wir kochen jedes Jahr Marmelade aus Quitten vom eigenen Baum."),
            ("b", "Ich restauriere alte Fahrraeder und verkaufe sie generalueberholt."),
            ("c", "Meine Modelleisenbahn im Keller hat ueber vierzig Meter Gleis."),
            ("d", "Ich zuechte seltene Orchideen auf dem Balkon meiner Wohnung."),
        ]
    ]
    lead = _lead(
        "Wir kochen jedes Jahr Marmelade aus Quitten vom eigenen Baum, das ist "
        "Tradition. Ich restauriere seit Jahren alte Fahrraeder und verkaufe sie "
        "generalueberholt weiter. Meine Modelleisenbahn im Keller hat mittlerweile "
        "ueber vierzig Meter Gleis. Seit letztem Jahr zuechte ich auch seltene "
        "Orchideen auf dem Balkon meiner Wohnung."
    )
    signals = JobRequirementSignalDetector(facts).detect(lead)
    assert len(signals) == 3
    assert len({s.kind for s in signals}) == 3, "Signale muessen aus verschiedenen Fakten stammen"

    assert len(JobRequirementSignalDetector(facts, max_signals=2).detect(lead)) == 2
    # More slots than qualifying facts must not invent padding.
    assert len(JobRequirementSignalDetector(facts, max_signals=10).detect(lead)) == 4
