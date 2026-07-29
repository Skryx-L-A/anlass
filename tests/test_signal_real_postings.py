"""Stage 5 against two example postings modeled on the first real run (29.07.2026).

The posting texts below are invented, fictional ads - they carry the properties the
first real run actually measured (a literal sentence count, a forbidden and a
required artifact, a Claude Code requirement, ...) without reproducing any real
employer's wording, same convention already used by ``tests/test_enrich_extract.py``.

The other tests here use invented facts against the example posting text to show
that the ranking (`anlass/signal/detect.py`) surfaces the sharpest requirement
sentence once a fact actually backs it - the fix for finding Q3 found during the
quality pass.
"""

from __future__ import annotations

import json

from anlass.models import Fact, Field, FieldValue, Lead
from anlass.profile import load_facts
from anlass.signal.detect import JobRequirementSignalDetector

# Same fictional postings as tests/test_enrich_extract.py - kept in sync there.
NORDLICHT_TEXT = (
    "Nordlicht baut KI-gestuetzte Planungssysteme fuer Handwerksbetriebe, die "
    "Auftraege, Kapazitaeten und Materialbestellungen automatisch aufeinander "
    "abstimmen. Wir entwickeln lernende Assistenten, die eingehende Anfragen "
    "sortieren, freie Kapazitaeten pruefen und Einsatzplaene vorschlagen.\n\n"
    "Deine Aufgaben: KI-gestuetzte Planungslogik und Automatisierungen mit "
    "eigenen Backend-Diensten entwickeln, die manuelle Abstimmung ersetzen. "
    "Schnittstellen zu Handwerkersoftware und Lagerverwaltung bauen. Modelle "
    "fuer Bedarfsvorhersage trainieren und auswerten. Wiederkehrende "
    "Handgriffe im Team finden und durch automatisierte Ablaeufe ersetzen. "
    "Direkt mit dem Gruenderteam an der Planungssoftware arbeiten.\n\n"
    "Dein Profil: Du studierst aktuell oder hast einen Bachelor oder Master "
    "abgeschlossen - beides passt, auch berufsbegleitend. Du hast nachweislich "
    "schon etwas mit KI gebaut oder automatisiert (GPT, n8n, eigene Skripte). "
    "Du denkst in Systemen statt in Einzelaufgaben und lieferst messbaren "
    "Output. Du bist schnell, eigenstaendig und bringst Projekte zu Ende. "
    "Verhandlungssicheres Deutsch, sicheres Englisch.\n\n"
    "Was wir bieten: Du lernst, Planungssysteme zu bauen, die echte Ablaeufe "
    "ersetzen - eine gefragte Faehigkeit. Echte Verantwortung ab Tag eins. "
    "Direkte Zusammenarbeit mit dem Gruenderteam. Klarer Weg in eine "
    "Festanstellung, wenn es passt.\n\n"
    "Rahmen: 3 Monate, Vollzeit, hybrid am Firmensitz. Ohne Bachelor 1.000 bis "
    "1.200 Euro im Monat.\n\n"
    "Bewerbung: Klick auf 'Jetzt bewerben' und beschreibe in 3 Saetzen, welches "
    "KI-Projekt oder welche Automatisierung du zuletzt gebaut hast. Bonus: ein "
    "kurzes Video, das deinen aktuellen Workflow oder eine fertige Umsetzung "
    "zeigt."
)

TALWERK_TEXT = (
    "Talwerk baut interne Entwicklerwerkzeuge fuer Handelsunternehmen: "
    "Dashboards, Lagerintegrationen und Automatisierungen rund um den "
    "Checkout.\n\n"
    "Ihre Aufgaben: Neue Plattform-Features testen, gemeldete Fehler "
    "nachstellen, laufend an der Performance arbeiten, interne Werkzeuge "
    "bauen und alles sauber in der Dokumentation festhalten.\n\n"
    "Stack: TypeScript/Node, Next.js, Airtable, Claude Code.\n\n"
    "Voraussetzungen: eingeschriebene:r Student:in (Informatik, "
    "Wirtschaftsinformatik oder nachweisbare Programmiererfahrung). Backend "
    "in TypeScript oder Python. Git, Kommandozeile, SQL-Grundlagen. Claude "
    "Code Pflicht - produktiv im taeglichen Coding-Workflow, nicht zum "
    "Ausprobieren. Selbststaendigkeit und Problemloeser-Mentalitaet. Sie "
    "haben ein Automatisierungs- oder Integrations-Projekt mit echten "
    "Nutzern oder echtem Live-Einsatz gebaut - klein ok, Hauptsache live. "
    "Beruehrung mit mindestens einem unserer Werkzeuge: Airtable-API, n8n, "
    "Claude Code.\n\n"
    "Rahmen: remote-first mit optionalem Buero. 20 Stunden pro Woche im "
    "Semester, bis 40 in den Semesterferien, 20 Euro pro Stunde. Alternativ "
    "ein mindestens sechsmonatiges Vollzeitpraktikum. Klare Uebernahmeoption "
    "in eine feste Engineering-Rolle nach dem Abschluss.\n\n"
    "Bewerbung: Pitch per Mail an bewerbung@talwerk.example oder per "
    "LinkedIn an Mira Halden. Schicken Sie uns einen GitHub-Link auf ein "
    "Automatisierungs- oder Integrations-Projekt, das Sie gebaut haben, und "
    "zwei Saetze, warum genau diese Rolle. Keine Anschreiben."
)


def _lead(text: str) -> Lead:
    return Lead(source="datei", text=text)


def test_a_fact_backing_the_sharpest_requirement_beats_the_generic_task_list():
    """Reproduces Q3 and shows the fix, given a fact base that actually supports it.

    ``general_dev_practice`` deliberately shares several words with the Talwerk task
    list ("Plattform-Features", "Werkzeuge", "Performance", "Dokumentation" all
    recur) - the same mechanism that let an overly generic task-description fact win
    by sharing a bit of vocabulary with almost every sentence. ``claude_code`` shares
    fewer words overall but they are the specific, on-point ones. With the old
    raw-overlap-count ranking the task list would win here too; with the
    length-normalised, rarity-weighted, requirement-boosted ranking it does not.
    """
    general_dev_practice = Fact(
        id="general-dev-practice",
        claim=(
            "Ich teste regelmaessig neue Plattform-Features, baue interne Werkzeuge "
            "und verbessere die Performance meiner Systeme kontinuierlich, dazu "
            "gehoert auch Dokumentation und Fehleranalyse im laufenden Betrieb."
        ),
        source="Erfundenes Beispiel fuer den Test",
    )
    claude_code = Fact(
        id="claude-code-taeglich",
        claim="Ich benutze Claude Code taeglich produktiv in meinem eigenen Coding-Workflow.",
        source="Erfundenes Beispiel fuer den Test",
    )
    signals = JobRequirementSignalDetector([general_dev_practice, claude_code]).detect(_lead(TALWERK_TEXT))
    assert signals
    assert signals[0].kind == "requirement:claude-code-taeglich"
    assert "Claude Code Pflicht" in signals[0].quote


def test_the_two_real_postings_do_not_share_the_same_backing_fact():
    """Same scenario, richer invented fact base covering both postings at once."""
    facts = [
        Fact(
            id="claude-code-taeglich",
            claim="Ich benutze Claude Code taeglich produktiv in meinem eigenen Coding-Workflow.",
            source="Erfundenes Beispiel fuer den Test",
        ),
        Fact(
            id="agenten-orchestrierung",
            claim=(
                "Ich baue und orchestriere eigene KI-Agenten, die Aufgaben "
                "recherchieren, schreiben und automatisieren."
            ),
            source="Erfundenes Beispiel fuer den Test",
        ),
        Fact(
            id="general-dev-practice",
            claim=(
                "Ich teste regelmaessig neue Plattform-Features, baue interne "
                "Werkzeuge und verbessere die Performance meiner Systeme "
                "kontinuierlich."
            ),
            source="Erfundenes Beispiel fuer den Test",
        ),
    ]
    detector = JobRequirementSignalDetector(facts)
    top_nordlicht = detector.detect(_lead(NORDLICHT_TEXT))[0]
    top_talwerk = detector.detect(_lead(TALWERK_TEXT))[0]
    assert top_nordlicht.kind != top_talwerk.kind


def test_a_strength_beats_an_eligibility_fact_that_matches_more_literally():
    """Phase 6, finding 1: full marks on the weakest possible occasion.

    Both facts here match a sentence of the Nordlicht posting, and the language
    fact matches its sentence more literally - "Deutsch", "Englisch" and
    "verhandlungssicher" all recur, while the built-something fact shares only
    "gebaut" and the tool names. Similarity alone therefore opened the letter with
    "Zur Anforderung 'Verhandlungssicheres Deutsch, sicheres Englisch'", which every
    other applicant can write too. A fact that names something built and published
    outranks it now.
    """
    languages = Fact(
        id="sprachen",
        claim="Deutsch ist meine Erstsprache, Englisch spreche ich verhandlungssicher.",
        source="Erfundenes Beispiel fuer den Test",
    )
    built = Fact(
        id="agenten-gebaut",
        claim="Ich habe eine Agenten-Umgebung mit GPT und eigenen Skripten gebaut.",
        source="Erfundenes Beispiel, veroeffentlicht als Repo",
    )
    signals = JobRequirementSignalDetector([languages, built]).detect(_lead(NORDLICHT_TEXT))

    assert signals
    assert signals[0].kind == "requirement:agenten-gebaut"
    assert "KI gebaut oder automatisiert" in signals[0].quote


def test_a_full_fact_base_quotes_the_sharpest_sentence_of_each_posting(tmp_path):
    """The phase-6 acceptance check for anlass' own posting quality.

    This used to read the user's live profile at ``~/.config/anlass/profile`` by path
    and skip wherever that profile was absent - which meant the check either tested
    the state of one particular machine (pass/fail depending on what facts happened to
    be configured there) or tested nothing at all (skip everywhere else). Neither is a
    test. It now writes its own disposable ``facts.yaml`` and ``ausschreibungen.json``
    under ``tmp_path`` - invented facts, the same two fictional postings as the rest of
    this file - and loads them through the exact same code path
    (:func:`anlass.profile.load_facts` and :func:`json.loads`) production does. Runs
    everywhere, unconditionally.

    This replaces the phase-5 check, which asserted only that the two postings must
    not end up with the same *backing fact*. That criterion turned out to measure the
    wrong thing: since ``claude-code-taeglich`` was added to the fact base, the same
    strength legitimately answers both postings - Nordlicht asks for something built
    with KI, Talwerk demands Claude Code in daily use, and one fact covers both. What
    has to differ is the sentence that is quoted back, and that is what is asserted
    here. The phase-5 docstring's reason for not asserting the Talwerk sentence ("no
    fact mentions Claude or Code at all") also expired with that fact.
    """
    facts_file = tmp_path / "facts.yaml"
    facts_file.write_text(
        """
facts:
  - id: agenten-gebaut
    claim: "Ich habe eine Agenten-Umgebung mit GPT und eigenen Skripten gebaut."
    source: "Erfundenes Beispiel, veroeffentlicht als Repo"
  - id: person-sprachen
    claim: "Deutsch ist meine Erstsprache, Englisch spreche ich verhandlungssicher."
    source: "Erfundenes Beispiel fuer den Test"
  - id: claude-code-taeglich
    claim: "Ich benutze Claude Code taeglich produktiv in meinem eigenen Coding-Workflow."
    source: "Erfundenes Beispiel fuer den Test"
""",
        encoding="utf-8",
    )
    postings_file = tmp_path / "ausschreibungen.json"
    postings_file.write_text(
        json.dumps(
            [
                {"organization": "Nordlicht Systeme GmbH", "text": NORDLICHT_TEXT},
                {"organization": "Talwerk", "text": TALWERK_TEXT},
            ]
        ),
        encoding="utf-8",
    )

    facts = load_facts(facts_file)
    postings = json.loads(postings_file.read_text(encoding="utf-8"))
    detector = JobRequirementSignalDetector(facts)

    signals_by_org: dict[str, list] = {}
    for entry in postings:
        posting_lead = Lead(source="datei", text=entry["text"])
        if entry.get("url"):
            posting_lead.set(Field.URL, FieldValue(entry["url"], provider="datei"))
        signals = detector.detect(posting_lead)
        assert signals, f"{entry['organization']}: kein Anlass gefunden"
        signals_by_org[entry["organization"]] = signals

    nordlicht = signals_by_org["Nordlicht Systeme GmbH"][0].quote
    talwerk = signals_by_org["Talwerk"][0].quote
    # The strength, not the language requirement: "Verhandlungssicheres Deutsch" won
    # this before the strength bonus and produced a full-marks letter that said nothing
    # about the applicant.
    assert "KI gebaut oder automatisiert" in nordlicht
    assert "Claude Code Pflicht" in talwerk
    assert nordlicht != talwerk

    # The bonus ranks occasions; it must not decide which fact backs a sentence. An
    # earlier version applied it while the backing fact was being chosen, and the
    # language sentence was then backed by the latency measurement - which names
    # Deutsch and Englisch too. The quote stayed the weak one and its evidence turned
    # absurd, which is worse than the bug being fixed.
    language_sentence = "Verhandlungssicheres Deutsch, sicheres Englisch."
    backing = {
        signal.quote: signal.kind for signal in signals_by_org["Nordlicht Systeme GmbH"]
    }
    assert backing.get(language_sentence) == "requirement:person-sprachen"
