"""Shared fixtures.

No test in this suite touches the network, sends mail, plays audio or opens a window.
Language models are always the deterministic stand-in; the only subprocess that runs
is a Python one-liner started by the test itself.

All content here is invented. Nothing from a real fact base ends up in the suite.
"""

from __future__ import annotations

import pytest

from anlass.models import Draft, Fact, FieldValue, Lead, Paragraph, Signal

SENDER = "Mara Lindqvist"


@pytest.fixture
def facts() -> list[Fact]:
    return [
        Fact(
            id="cli-crossplatform",
            claim="Das Werkzeug laeuft auf Linux, Windows und macOS.",
            source="beispiel.example/werkzeug, Fassung 1.4.0",
        ),
        Fact(
            id="pipeline-latenz",
            claim="Die Verarbeitungskette wurde von 7,5 auf 3,8 Sekunden je Durchlauf gesenkt.",
            source="Messprotokoll vom 12.03.2026, sieben Durchlaeufe",
        ),
        Fact(
            id="pipeline-fehler",
            claim=(
                "Ein Umbau auf einen gemeinsamen Datenbus schlug fehl und wurde durch zwei "
                "getrennte Busse ersetzt."
            ),
            source="Projekttagebuch, Eintrag vom 04.02.2026",
        ),
        Fact(
            id="tests-abdeckung",
            claim="Die Testsuite deckt alle oeffentlichen Funktionen ab.",
            source="Datei .github/workflows/tests.yml",
        ),
    ]


@pytest.fixture
def lead() -> Lead:
    entry = Lead(
        source="datei",
        source_ref="beispiel-ausschreibung.txt",
        text=(
            "Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung. "
            "Ansprechpartnerin ist Frau Roth."
        ),
    )
    entry.set("organization", FieldValue("Nordlicht Systeme", provider="datei"))
    entry.set("role", FieldValue("Werkstudent Datenverarbeitung", provider="datei"))
    entry.set("contact_name", FieldValue("Roth", provider="datei"))
    entry.set("remote", FieldValue(True, provider="extraktion", confidence=0.7))
    return entry


@pytest.fixture
def signal(lead: Lead) -> Signal:
    quote = "Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung."
    return Signal(
        lead_id=lead.id,
        kind="eigenbau",
        quote=quote,
        source_url="beispiel.example/stelle",
        start=lead.text.find(quote),
        end=lead.text.find(quote) + len(quote),
        detector="stichwort",
    )


def make_draft(lead: Lead, signal: Signal, paragraphs: list[tuple[str, tuple[str, ...]]]) -> Draft:
    """Build a draft from (text, fact ids) pairs, bypassing the model."""
    return Draft(
        lead_id=lead.id,
        signal_id=signal.id,
        subject="Ihre Ausschreibung",
        paragraphs=[Paragraph(text=text, fact_ids=ids) for text, ids in paragraphs],
        model="fake:test",
    )


#: A draft that is entirely covered. Nothing in it may be reported as an error.
CLEAN_PARAGRAPHS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Sehr geehrte Frau Roth, in Ihrer Ausschreibung steht, dass Sie Ihre "
        "Datenverarbeitung selbst bauen.",
        (),
    ),
    (
        "Ich habe eine Verarbeitungskette von 7,5 auf 3,8 Sekunden je Durchlauf "
        "gebracht, indem der Erkennungsschritt dauerhaft laeuft.",
        ("pipeline-latenz",),
    ),
    (
        "Ein Umbau auf einen gemeinsamen Datenbus schlug dabei fehl; die Ursache habe "
        "ich gemessen und auf zwei getrennte Busse umgebaut.",
        ("pipeline-fehler",),
    ),
    (
        # Long enough that the body clears the minimum on its own. Since the sign-off
        # stopped counting towards length - it is nobody's argument - a fixture that
        # only reached sixty words with the greeting included no longer does. The
        # fixture was passing on that accident; the rule is what stayed right.
        "Das Werkzeug laeuft auf Linux, Windows und macOS, und die Testsuite deckt die "
        "oeffentlichen Funktionen ab, damit ein spaeterer Umbau nichts still zerbricht "
        "und jeder Schritt sich einzeln austauschen laesst, ohne dass die uebrigen "
        "davon etwas mitbekommen.",
        ("cli-crossplatform", "tests-abdeckung"),
    ),
    (
        "Haetten Sie Zeit fuer ein kurzes Gespraech? Viele Gruesse, Mara Lindqvist",
        (),
    ),
]
