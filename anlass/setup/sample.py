"""The shipped example posting, and the dry run that goes through the whole chain.

Why a dry run belongs to the setup: a tool nobody has seen work is a tool nobody hands
their own access data to. So the first run goes against an example, with an invented
fact base, and shows what every stage did.

**It goes through the same wiring a real run does** (:func:`anlass.wiring.build_pipeline`),
not through a shortened copy of it. A demonstration that took a different path than the
tool would be a demonstration of something else. What differs is only what a dry run
must not do:

* the model may be the stand-in below instead of a configured provider,
* the enrichment providers that fetch pages are left out (``offline=True``),
* the transport is :class:`~anlass.transport.file.FileTransport` into a directory the
  caller supplies, and the store is a database the caller supplies - both meant to be
  temporary, so the run leaves nothing behind,
* the actor on the approval record is ``trockenlauf`` and not a person, because nobody
  read this draft.

Nothing here reaches the network, and nothing here sends.

Everything in here is invented. No name, no number and no address in this module refers
to a real organisation, and none of it may end up in a user's profile: the skeleton
``anlass init`` writes is empty on purpose (see :mod:`anlass.setup.templates`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..interfaces import LLM, Store
from ..llm.fake import FakeLLM
from ..models import Fact, FieldValue, Lead, RawRecord
from ..pipeline import Pipeline
from ..profile import Profile
from ..wiring import build_pipeline

__all__ = [
    "SAMPLE_CRITERIA",
    "SAMPLE_FACTS",
    "SAMPLE_POSTING",
    "SAMPLE_RECIPIENT",
    "SAMPLE_SENDER",
    "SAMPLE_VOICE",
    "STAND_IN_DRAFT",
    "SampleSource",
    "build_sample_pipeline",
    "stand_in_llm",
]

SAMPLE_SENDER = "Jonna Reuter"

#: Invented address. ``.example`` is reserved for documentation and cannot be
#: registered, so a mistake here still cannot reach anybody.
SAMPLE_RECIPIENT = "adler@halbinsel-datentechnik.example"

SAMPLE_POSTING = """Halbinsel Datentechnik GmbH sucht Werkstudentin oder Werkstudenten Datenverarbeitung

Wir bauen unsere Datenverarbeitung selbst und suchen dafuer Verstaerkung. Unsere
Auswertungen laufen heute als nachts gestartete Skripte; wir wollen daraus eine Kette
machen, die den ganzen Tag laeuft und der man ansieht, woher eine Zahl stammt.

Was wir uns wuenschen: Du hast schon einmal etwas gebaut, das laenger als eine Woche in
Betrieb war, und kannst erzaehlen, was dabei schiefging. Python ist unser Hauptwerkzeug.
Die Arbeit ist zu grossen Teilen aus der Ferne moeglich, zwanzig Stunden pro Woche.

Wir sind vierzehn Leute. Bei Fragen meldest du dich bei Frau Adler.
"""

#: Invented fact base for the dry run. Short on purpose: the point is to show the chain,
#: not to write a good letter.
SAMPLE_FACTS: tuple[Fact, ...] = (
    Fact(
        id="kette-dauerbetrieb",
        claim=(
            "Eine naechtliche Skriptsammlung wurde in eine dauerhaft laufende Kette "
            "umgebaut, die seither ohne manuellen Start arbeitet."
        ),
        source="Projekttagebuch, Eintrag vom 11.01.2026",
    ),
    Fact(
        id="kette-herkunft",
        claim=(
            "Zu jedem Ergebnis der Kette wird festgehalten, aus welchem Feld und von "
            "welchem Schritt es stammt."
        ),
        source="Beispielprojekt, Datei zur Herkunftsverfolgung",
    ),
    Fact(
        id="umbau-fehlschlag",
        claim=(
            "Ein Umbau auf einen gemeinsamen Datenbus schlug fehl und wurde durch zwei "
            "getrennte Busse ersetzt, nachdem die Ursache gemessen statt vermutet wurde."
        ),
        source="Projekttagebuch, Eintrag vom 04.02.2026",
    ),
    Fact(
        id="python-werkzeuge",
        claim="Die genannten Werkzeuge sind in Python geschrieben und mit Tests versehen.",
        source="Beispielprojekt, Testlauf vom 20.02.2026",
    ),
)

#: Criteria for the dry run, in the shape ``profile.example/criteria.yaml`` documents.
#: The dry run carries its own copy instead of reading the user's file: it runs before
#: a user has written criteria, and it must produce the same result on every machine.
#: The numbers are chosen so the example posting passes - a demonstration that stopped
#: at stage 4 would show the chain working, but not the part people came to see.
SAMPLE_CRITERIA: dict[str, Any] = {
    "criteria": [
        {
            "name": "remote_or_nearby",
            "weight": 3,
            "check": "remote == true",
        },
        {
            "name": "build_it_yourself",
            "weight": 3,
            "check": "text_contains_any([bauen, implementieren, eigenverantwortlich])",
        },
        {
            "name": "doable_alongside_studies",
            "weight": 2,
            "check": "employment_type in [werkstudent, praktikum, teilzeit]",
        },
        {
            "name": "small_organization",
            "weight": 2,
            "check": "headcount < 200",
        },
        {
            "name": "contact_person_known",
            "weight": 1,
            "check": "contact_name != null",
        },
    ],
    "exclusions": ["score < 6"],
    "limits": {
        "max_sends_per_day": 5,
        "days_between_same_organization": 30,
        "min_score": 6,
    },
}

SAMPLE_VOICE = """Sachlich, knapp, ohne Anbiederung. Erst der Anlass, dann was ich gebaut
habe, dann eine Frage, die man mit einem Satz beantworten kann. Keine Superlative, keine
Ausrufezeichen, keine Zahlen ohne Messung dahinter. Gruss: "Viele Gruesse", darunter mein
Name.
"""

#: What the stand-in answers when no real model is reachable. Written to pass the
#: verification pass - a dry run that blocks on its own example would teach the wrong
#: thing about the tool.
STAND_IN_DRAFT = """{
  "subject": "Ihre Ausschreibung: Datenverarbeitung selbst bauen",
  "paragraphs": [
    {"text": "Sehr geehrte Frau Adler, in Ihrer Ausschreibung steht, dass Sie Ihre Datenverarbeitung selbst bauen und aus den nachts gestarteten Skripten eine durchlaufende Kette machen wollen.", "fact_ids": []},
    {"text": "Genau diesen Umbau habe ich hinter mir: aus einer naechtlichen Skriptsammlung wurde eine dauerhaft laufende Kette, die ohne manuellen Start arbeitet. Zu jedem Ergebnis wird festgehalten, aus welchem Feld und von welchem Schritt es stammt.", "fact_ids": ["kette-dauerbetrieb", "kette-herkunft"]},
    {"text": "Schiefgegangen ist dabei der Umbau auf einen gemeinsamen Datenbus. Ich habe die Ursache gemessen statt vermutet und auf zwei getrennte Busse umgebaut.", "fact_ids": ["umbau-fehlschlag"]},
    {"text": "Haetten Sie Zeit fuer ein kurzes Gespraech? Viele Gruesse, Jonna Reuter", "fact_ids": []}
  ]
}"""


#: What the stand-in answers the extraction provider (stage 3). Two fields the example
#: posting states in prose and the source therefore does not fill verbatim - enough for
#: the enrichment step to show a real result with its provenance instead of an excuse.
_STAND_IN_EXTRACTION = """{
  "fields": {
    "required_skills": {
      "value": ["etwas gebaut, das laenger als eine Woche in Betrieb war", "Python"],
      "evidence": "Du hast schon einmal etwas gebaut, das laenger als eine Woche in Betrieb war"
    },
    "contact_channel": {
      "value": "Rueckfragen an Frau Adler",
      "evidence": "Bei Fragen meldest du dich bei Frau Adler."
    }
  }
}"""


def stand_in_llm() -> FakeLLM:
    """The deterministic stand-in for a dry run without a reachable model.

    Named for what it is. A dry run that quietly used a canned answer while the user
    believed their model wrote it would be the exact opposite of what this tool sells.

    Answers per prompt, not per call number: stage 3 and stage 6 both ask, and which of
    them asks first is a property of the chain that a fixed list of answers would tie
    itself to. Each stage's prompt carries a marker no other prompt has.
    """
    return FakeLLM(
        rules={"FAKTENLISTE": STAND_IN_DRAFT, "AUSSCHREIBUNG": _STAND_IN_EXTRACTION},
        label="trockenlauf",
    )


@dataclass
class SampleSource:
    """Stages 1 and 2 for the dry run: one shipped posting, normalised into a lead.

    This is the demonstration fixture, not a shipped production source. The real ones
    (file, URL, RSS, career page, open job API) live in ``anlass/sources/``.
    """

    text: str = SAMPLE_POSTING

    @property
    def name(self) -> str:
        return "beispiel"

    def fetch(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> Iterator[RawRecord]:
        if limit is not None and limit <= 0:
            return
        yield RawRecord(
            source=self.name,
            external_id="beispiel-ausschreibung",
            text=self.text,
            url=None,
        )

    def normalize(self, record: RawRecord) -> Lead:
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        verbatim = {
            "organization": "Halbinsel Datentechnik GmbH",
            "role": "Werkstudent Datenverarbeitung",
            "contact_name": "Adler",
            "contact_email": SAMPLE_RECIPIENT,
            "employment_type": "werkstudent",
            "workload_hours": 20,
            "headcount": 14,
            "remote": True,
            "tech_stack": "Python",
        }
        for name, value in verbatim.items():
            lead.set(name, FieldValue(value, provider=self.name, evidence=record.text[:200]))
        return lead


def build_sample_pipeline(
    llm: LLM | None = None,
    *,
    store: Store,
    outbox: str | Path,
    facts: Sequence[Fact] = SAMPLE_FACTS,
    with_model_layer: bool = False,
) -> Pipeline:
    """The whole chain, wired for the dry run.

    Args:
        llm: The configured model. ``None`` uses :func:`stand_in_llm`.
        store: Where the run is written down. Required, not optional: stage 8 reads the
            score and today's releases out of it, so a chain without one could not
            enforce the rules this run is supposed to demonstrate.
        outbox: Directory the file transport writes into. Meant to be temporary.
        facts: The fact base for the dry run.
        with_model_layer: Whether verification also asks the model. Off by default: the
            deterministic floor works offline and decides on its own anyway.
    """
    model = llm if llm is not None else stand_in_llm()
    profile = Profile(
        facts=list(facts),
        voice=SAMPLE_VOICE,
        criteria=dict(SAMPLE_CRITERIA),
    )
    return build_pipeline(
        profile=profile,
        config={"mailbox": {"transport": "datei", "directory": str(outbox)}},
        store=store,
        source=SampleSource(),
        llm=model,
        offline=True,
        verify_with_model=with_model_layer,
        actor="trockenlauf",
        extra_grounding=(SAMPLE_SENDER,),
    )
