"""Stage 6: generate a draft from facts, lead and signal - from nothing else.

The generator receives exclusively the fact entries plus the lead plus the signal, and
must return the used ids per paragraph. Everything it could otherwise draw on (an open
web search, the model's world knowledge about the organisation, an earlier draft) is
simply not passed in. That is the cheapest half of the grounding: what never enters
cannot be cited.

The other half is :mod:`anlass.draft.verify`, and it is a separate pass on purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..critique._text import body_sentence_count, is_closing, is_salutation, sentences
from ..errors import DraftError
from ..interfaces import LLM
from ..models import Draft, Fact, Field, Lead, Paragraph, Signal, facts_by_id
from ._parse import extract_json

__all__ = [
    "FactGroundedDrafter",
    "build_prompt",
    "sentence_limit",
    "MAX_FACTS_PER_PARAGRAPH",
]

#: Q4: five facts strung together in one paragraph reads like a checklist, not an
#: argument. A paragraph carries one thought and uses at most this many.
MAX_FACTS_PER_PARAGRAPH = 3

#: Q2's exact failure: a formal opening slipped in even though the posting duzt.
_SIE_OPENING = "Sehr geehrte"
#: Word-boundary so this never matches "Duisburg", "Dur" or similar.
_DU_PRONOUNS = re.compile(r"\b(Du|Dein|Deine|Deinem|Deinen|Deiner|Dich|Dir)\b")

SYSTEM_PROMPT = (
    "Du schreibst kurze, sachliche deutsche Erstanschreiben. Du verwendest "
    "ausschliesslich die Aussagen aus der uebergebenen Faktenliste. Du erfindest "
    "keine Zahlen, keine Namen, keine Produkte und keine Steigerungen. Wenn eine "
    "Aussage nicht in der Liste steht, laesst du sie weg. Die BEWERBUNGSVORGABE, "
    "falls uebergeben, ist eine Tatsache ueber die Gegenseite, keine Anregung: was "
    "dort als nicht gewuenscht steht, erzeugst du nicht, was dort als Obergrenze "
    "steht, haeltst du ein. Du antwortest nur mit JSON."
)

_INSTRUCTIONS = """Schreibe einen Entwurf fuer eine Erstansprache.

Regeln, die nicht verhandelbar sind:
1. Jede Tatsachenaussage im Text muss aus der Faktenliste stammen. Gib je {unit} die
   verwendeten Kennungen an.
2. Zahlen, Eigennamen, Produktnamen und Steigerungen nur, wenn sie woertlich in der
   Faktenliste, in den Empfaengerdaten oder im Anlass-Zitat vorkommen.
3. {structure}
4. Keine Emojis, keine Ausrufezeichen, keine Werbesprache.
5. Gibt die Faktenliste ein ausgeliefertes, oeffentlich sichtbares Ergebnis her
   (nicht nur eine Arbeitsweise), nennst du mindestens eines davon. Zieh die Belege
   nicht ausschliesslich aus einem einzigen Thema.
6. Steht eine BEWERBUNGSVORGABE dabei, befolgst du sie woertlich: verlangte Form und
   Laenge, verlangter Kanal, verbotene Bestandteile, Anrede.

{schema}"""

#: The free-text shape: paragraphs, as many as the stage allows, counted by the model.
_PROSE_STRUCTURE = (
    "Kein Absatz ohne Inhalt. Hoechstens {max_paragraphs} Absaetze, hoechstens "
    "{max_facts_per_paragraph} Belege je Absatz. Ein Absatz traegt einen Gedanken - er\n"
    "   sammelt Belege nicht ein, er fuehrt sie zu einer Aussage."
)

_PROSE_SCHEMA = """Antworte ausschliesslich mit einem JSON-Objekt in dieser Form:
{"subject": "Betreffzeile", "paragraphs": [{"text": "...", "fact_ids": ["kennung"]}]}

Ein Absatz ohne Tatsachenaussage (Anrede, Gruss) bekommt eine leere Liste."""

#: The counted shape, used whenever the posting names a sentence bound. See
#: :func:`_list_schema` for why the answer is a list and not prose.
_LIST_STRUCTURE = (
    "Kein Eintrag ohne Inhalt, hoechstens {max_facts_per_paragraph} Belege je Eintrag. "
    "Ein Satz\n   traegt einen Gedanken - er sammelt Belege nicht ein."
)


def _list_schema(limit: int) -> str:
    """The answer shape that makes the sentence bound structural instead of counted.

    Counting its own sentence endings is what the model demonstrably cannot do: measured
    over four fresh runs on 29.07.2026, five of six rounds against the Talwerk posting
    broke the same two-sentence bound, after being told about it five times
    (Q19). Enumerating is easy where counting is hard - a list of
    exactly ``limit`` entries carries the number in its shape, and
    :meth:`FactGroundedDrafter._read_sentence_list` can check it before a word of the
    text is read.

    Salutation and sign-off get their own keys rather than being entries. That removes
    the second thing the model had to know: which paragraphs count towards the bound.
    Here they cannot count, because they are not in the list.
    """
    return f"""Antworte ausschliesslich mit einem JSON-Objekt in dieser Form:
{{"subject": "Betreffzeile", "salutation": "", "sentences": [{{"text": "genau ein Satz.", "fact_ids": ["kennung"]}}], "closing": ""}}

"sentences" enthaelt GENAU {limit} Eintraege - nicht mehr und nicht weniger. Ein Eintrag
ist genau ein Satz mit genau einem Satzzeichen am Ende, dazu die Kennungen, die diesen
einen Satz belegen. Zwei Saetze in einem Eintrag werden zurueckgewiesen, ein Eintrag zu
viel oder zu wenig ebenso.

"salutation" und "closing" sind Anrede und Grussformel. Sie stehen nicht in der Liste
und zaehlen nicht als Satz. Ist ein Anschreiben nicht gewuenscht, bleiben beide leer.
Sonst beginnt die Anrede mit "Hallo", "Guten Tag" oder "Sehr geehrte" und die
Grussformel mit "Viele Gruesse"."""

#: Human labels for :class:`anlass.models.Field` entries the application block reads.
#: The countable rules are not in here - they lead the prompt as their own block, see
#: :func:`_limits_block`.
_APPLICATION_FIELDS = (
    (Field.APPLICATION_FORMAT, "Verlangte Form"),
    (Field.APPLICATION_CHANNEL, "Kanal"),
    (Field.REQUIRED_ARTIFACTS, "Verlangt"),
    (Field.FORBIDDEN_ARTIFACTS, "Nicht gewuenscht - erzeuge nichts davon"),
    (Field.CONTACT_NAME, "Namentlich an"),
)


def _application_block(lead: Lead) -> str | None:
    """The posting's own application instructions, framed as fact - see Q1/Q2.

    Returns ``None`` when the lead carries none of these fields, so ``build_prompt``
    can omit the block entirely rather than print an empty one.
    """
    lines: list[str] = []
    for name, label in _APPLICATION_FIELDS:
        value = lead.value(name)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{label}: {value}")
    if not lines:
        return None
    return "BEWERBUNGSVORGABE (Tatsache aus der Ausschreibung, keine Bitte):\n" + "\n".join(lines)


def sentence_limit(lead: Lead) -> int | None:
    """The posting's sentence bound as a usable number, or ``None``.

    ``None`` for an unset, unreadable or non-positive value: all three mean the same
    thing here, namely that this posting does not bound the answer and the draft comes
    back as free prose.
    """
    raw = lead.value(Field.MAX_SENTENCES)
    if raw is None:
        return None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def _limits_block(lead: Lead, max_paragraphs: int) -> str:
    """The countable rules, first in the prompt, with a rule for how to count.

    These are the rules this stage refuses its own answer over
    (:meth:`FactGroundedDrafter._enforce_limits`), so the model has to see them before
    anything else. They used to sit halfway down the application block as one label
    among seven ("Hoechstens so viele Saetze insgesamt: 3"), which is where a sentence
    cap gets read as a preference.

    Measured on 29.07.2026, eight calls per variant against the Nordlicht posting
    (cap: three sentences): with the cap in the application block 2 of 8 answers kept
    it, with this block in front 4 of 8. Twice as good and still a coin flip - at eight
    samples that difference is not decisive, and either way it is nowhere near enough on
    its own. **The wording is not what makes the limit hold; the loop is.** This block
    is kept because it is free, it never scored worse, and a countable rule belongs
    where it cannot be read as a preference.
    """
    lines: list[str] = []
    limit = sentence_limit(lead)
    if limit is not None:
        # Phase 8: the number is carried by the answer's shape, so this says which
        # shape rather than asking for a count the model cannot do.
        lines.append(
            f"- Hoechstens {limit} Saetze insgesamt. Deshalb antwortest du mit einer "
            f"Liste aus genau {limit} Eintraegen, je Eintrag genau ein Satz."
        )
    form = lead.value(Field.FORM_OF_ADDRESS)
    if form == "du":
        lines.append("- Anrede: Du-Form. Kein 'Sehr geehrte', kein 'Sie', kein 'Ihre'.")
    elif form == "sie":
        lines.append("- Anrede: Sie-Form. Kein 'Du', kein 'Dein', kein 'Dich'.")
    if limit is None:
        lines.append(
            f"- Hoechstens {max_paragraphs} Absaetze, hoechstens "
            f"{MAX_FACTS_PER_PARAGRAPH} Belege je Absatz."
        )
    else:
        lines.append(f"- Hoechstens {MAX_FACTS_PER_PARAGRAPH} Belege je Eintrag.")
    return (
        "HARTE GRENZEN (gezaehlt, nicht geschaetzt - sie gehen jeder Stilfrage vor. "
        "Ein Entwurf, der eine davon reisst, wird verworfen):\n" + "\n".join(lines)
    )


def build_prompt(
    lead: Lead,
    signal: Signal | None,
    facts: Sequence[Fact],
    voice: str = "",
    *,
    max_paragraphs: int = 4,
    revision_notes: Sequence[str] = (),
) -> str:
    """Assemble the prompt. Exposed so a test can assert what the model does not see.

    Two shapes, and the posting decides which: with a sentence bound the answer is a
    list of exactly that many entries (see :func:`_list_schema`), without one it is free
    prose in paragraphs. A letter is prose, and nothing here changes that.
    """
    limit = sentence_limit(lead)
    if limit is None:
        structure = _PROSE_STRUCTURE.format(
            max_paragraphs=max_paragraphs, max_facts_per_paragraph=MAX_FACTS_PER_PARAGRAPH
        )
        schema, unit = _PROSE_SCHEMA, "Absatz"
    else:
        structure = _LIST_STRUCTURE.format(max_facts_per_paragraph=MAX_FACTS_PER_PARAGRAPH)
        schema, unit = _list_schema(limit), "Eintrag"
    blocks: list[str] = [
        _limits_block(lead, max_paragraphs),
        _INSTRUCTIONS.format(structure=structure, schema=schema, unit=unit),
    ]

    fact_lines = [f"[{fact.id}] {fact.claim} (Beleg: {fact.source})" for fact in facts]
    blocks.append("FAKTENLISTE (nichts ausserhalb dieser Liste ist belegt):\n" + "\n".join(fact_lines))

    recipient = [f"Kennung: {lead.id}"]
    for name, value in sorted(lead.fields.items()):
        recipient.append(f"{name}: {value.value} (Quelle: {value.provider})")
    blocks.append("EMPFAENGER:\n" + "\n".join(recipient))

    application_block = _application_block(lead)
    if application_block is not None:
        blocks.append(application_block)

    if signal is not None:
        blocks.append(
            "ANLASS (woertliches Zitat aus der Quelle, Art: "
            f"{signal.kind}):\n\"{signal.quote}\""
        )
    else:
        blocks.append("ANLASS: keiner uebergeben. Behaupte keinen.")

    if voice.strip():
        blocks.append("STIMME (so klingt der Absender):\n" + voice.strip())

    if revision_notes:
        notes = "\n".join(f"- {note}" for note in revision_notes)
        blocks.append(
            "UEBERARBEITUNG (Rueckmeldung zu einem vorherigen Entwurf, bindend - "
            "jede Notiz muss sichtbar im neuen Text abgearbeitet sein):\n" + notes
        )

    return "\n\n".join(blocks)


def _count_sentences(paragraphs: Sequence[Paragraph]) -> int:
    """Sentences in the content-bearing paragraphs. See :func:`body_sentence_count`.

    Counted there and not here so that this stage and the rubric answer the question
    with the same number - they hand each other the consequence, and until phase 7 they
    counted differently: this one skipped every paragraph without a ``fact_ids`` entry,
    the rubric skipped none. A body paragraph the model left uncited is prose the
    recipient reads, so it counts; a salutation is not, so it does not.
    """
    return body_sentence_count(paragraph.text for paragraph in paragraphs)


@dataclass
class FactGroundedDrafter:
    """Drafter that only ever passes the given facts to the model.

    Args:
        llm: The model to use for this stage (see :class:`anlass.llm.router.LLMRouter`).
        max_paragraphs: Upper bound handed to the model and enforced on the answer.
        temperature: Low by default; this stage is not supposed to be creative with
            facts.
    """

    llm: LLM
    max_paragraphs: int = 4
    temperature: float = 0.3
    max_tokens: int | None = 1500

    @property
    def name(self) -> str:
        return f"fact-grounded:{self.llm.name}"

    def draft(
        self,
        lead: Lead,
        signal: Signal | None,
        facts: Sequence[Fact],
        voice: str = "",
        revision_notes: Sequence[str] = (),
    ) -> Draft:
        """Produce a draft. See :class:`anlass.interfaces.Drafter` for the contract.

        Args:
            revision_notes: German instruction sentences from a previous review round
                ("Schreibe die Anrede in Du-Form"). Empty means a first attempt; a
                non-empty sequence is a revision and every note is put to the model
                as binding, unlike the rest of this prompt nothing here is enforced
                in code afterwards - there is no mechanical test for "was this note
                addressed" over free-form German text.
        """
        if not facts:
            raise DraftError("Ohne Faktenbasis wird kein Entwurf erzeugt.")
        known = facts_by_id(facts)
        prompt = build_prompt(
            lead,
            signal,
            facts,
            voice,
            max_paragraphs=self.max_paragraphs,
            revision_notes=revision_notes,
        )
        raw = self.llm.complete(
            prompt,
            system=SYSTEM_PROMPT,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        data = extract_json(raw)
        if data is None:
            raise DraftError("Das Modell lieferte kein auswertbares JSON-Objekt.")
        limit = sentence_limit(lead)
        if limit is None:
            paragraphs, message, violation = self._read_paragraphs(data, known), "", ""
        else:
            paragraphs, message, violation = self._read_sentence_list(data, known, limit)
        subject = data.get("subject")
        draft = Draft(
            lead_id=lead.id,
            paragraphs=paragraphs,
            signal_id=None if signal is None else signal.id,
            subject=subject.strip() if isinstance(subject, str) and subject.strip() else None,
            model=self.llm.name,
        )
        if message:
            raise DraftError(message, draft=draft, violation=violation)
        self._enforce_limits(lead, draft, structured=limit is not None)
        return draft

    def _enforce_limits(self, lead: Lead, draft: Draft, *, structured: bool = False) -> None:
        """Every countable rule this stage refuses its own answer over, in one place.

        Two sorts of rule, and they are checked together because the consequence is
        the same: the posting's own limits (sentence cap, form of address - Q1, Q2)
        and this stage's own bounds (paragraphs, evidence per paragraph - Q4).
        ``application_format``, ``application_channel`` and ``required_artifacts``
        stay prompt-only: whether a text "is" the requested short form or names the
        right artifact is a judgement call, not a regular expression, and forcing one
        here would just trade Q1 for a brittle false rejection.

        Every refusal carries the rejected draft, which is why this runs on a finished
        :class:`~anlass.models.Draft` rather than on the raw answer: the text exists,
        it broke a countable rule, and the caller has to be able to keep it. Measured
        on 29.07.2026, four rounds against the Nordlicht posting broke the same
        sentence cap and the run ended with no draft at all - four model calls thrown
        away over a defect that was already named.

        Args:
            structured: The answer came back in the counted shape, so its paragraph
                count is not a choice the model made but the sentence bound plus at
                most a salutation and a sign-off. Bounding it a second time here would
                refuse a draft for following the instruction: three demanded sentences
                plus a greeting and a sign-off are five paragraphs against a limit of
                four. The sentence bound itself is still checked - it is free, and a
                stage that trusts its own reader has no floor left.
        """
        if not structured and len(draft.paragraphs) > self.max_paragraphs:
            raise DraftError(
                f"Das Modell lieferte {len(draft.paragraphs)} Absaetze, erlaubt sind "
                f"hoechstens {self.max_paragraphs}.",
                draft=draft,
                violation="absatzanzahl",
            )
        unit = "Eintrag" if structured else "Absatz"
        for index, paragraph in enumerate(draft.paragraphs, start=1):
            if len(paragraph.fact_ids) > MAX_FACTS_PER_PARAGRAPH:
                raise DraftError(
                    f"{unit} {index} beruft sich auf {len(paragraph.fact_ids)} Belege, "
                    f"erlaubt sind hoechstens {MAX_FACTS_PER_PARAGRAPH} (Q4: ein "
                    f"{unit} traegt einen Gedanken).",
                    draft=draft,
                    violation="belege",
                )
        limit = sentence_limit(lead)
        if limit is not None:
            counted = _count_sentences(draft.paragraphs)
            if counted > limit:
                raise DraftError(
                    f"Die Ausschreibung verlangt hoechstens {limit} Saetze, der "
                    f"Entwurf hat {counted} in den Absaetzen, die etwas aussagen.",
                    draft=draft,
                    violation="satzanzahl",
                )
        text = "\n\n".join(p.text for p in draft.paragraphs)
        form = lead.value(Field.FORM_OF_ADDRESS)
        if form == "du" and _SIE_OPENING in text:
            raise DraftError(
                "Die Ausschreibung duzt, der Entwurf antwortet mit einer Sie-Anrede.",
                draft=draft,
                violation="anrede",
            )
        if form == "sie" and _DU_PRONOUNS.search(text):
            raise DraftError(
                "Die Ausschreibung siezt, der Entwurf enthaelt eine Du-Anrede.",
                draft=draft,
                violation="anrede",
            )

    def _read_paragraphs(self, data: dict, known: dict[str, Fact]) -> list[Paragraph]:
        """Read the free shape. Bounds are :meth:`_enforce_limits`' job.

        The split matters: everything refused here is unusable (no text, an id that
        names no fact), everything refused there is a finished text that broke a
        countable rule - and that one is kept.
        """
        raw_paragraphs = data.get("paragraphs")
        if not isinstance(raw_paragraphs, list) or not raw_paragraphs:
            raise DraftError("Das Modell lieferte keine Absaetze.")
        return [
            self._read_entry(entry, index, known, "Absatz")
            for index, entry in enumerate(raw_paragraphs, start=1)
        ]

    def _read_sentence_list(
        self, data: dict, known: dict[str, Fact], limit: int
    ) -> tuple[list[Paragraph], str, str]:
        """Read the counted shape: exactly ``limit`` entries of one sentence each.

        Returns the paragraphs plus the first violation of the shape, as a German
        message and a machine-comparable kind - both empty when the shape is right. The
        violation is handed back instead of raised because the caller has to build the
        draft first: a text that broke a countable rule is still a text, and it goes to
        the person rather than into the bin (Q18).

        What is checked here is checked *before a word of the answer is read*, which is
        the whole reason for this shape. The messages name the number that is wrong,
        because a note has to be followable: "vier Eintraege geliefert, drei erlaubt" is,
        "halte die Satzgrenze ein" was not, five rounds running (Q19).
        """
        raw_entries = data.get("sentences")
        if not isinstance(raw_entries, list) or not raw_entries:
            raw_paragraphs = data.get("paragraphs")
            if isinstance(raw_paragraphs, list) and raw_paragraphs:
                # The free shape where the counted one was asked for. The text is kept
                # and measured like any other refused round; insisting harder would
                # throw away an answer that may be perfectly good prose.
                return (
                    self._read_paragraphs(data, known),
                    f"Das Modell antwortete in Absaetzen statt mit der verlangten Liste "
                    f"aus genau {limit} Saetzen.",
                    "listenform",
                )
            raise DraftError("Das Modell lieferte keine Liste von Saetzen.")

        entries = [
            self._read_entry(entry, index, known, "Eintrag")
            for index, entry in enumerate(raw_entries, start=1)
        ]
        salutation = _frame_text(data.get("salutation"))
        closing = _frame_text(data.get("closing"))
        paragraphs: list[Paragraph] = []
        if salutation:
            paragraphs.append(Paragraph(text=salutation))
        paragraphs.extend(entries)
        if closing:
            paragraphs.append(Paragraph(text=closing))

        if len(entries) != limit:
            return (
                paragraphs,
                f"{len(entries)} Eintraege geliefert, verlangt sind genau {limit}.",
                "eintragsanzahl",
            )
        for index, entry in enumerate(entries, start=1):
            counted = len(sentences(entry.text))
            if counted > 1:
                return (
                    paragraphs,
                    f"Eintrag {index} enthaelt {counted} Saetze, je Eintrag ist genau "
                    f"einer erlaubt: \"{entry.text[:60]}\"",
                    "satz-je-eintrag",
                )
        # A frame paragraph that is not recognisable as one would be counted as a body
        # sentence by the rubric and by _enforce_limits - and a draft that keeps the
        # bound would lose points for keeping it. That is the class of defect this
        # rubric has produced three times already (Q12, Q15, Q17), always where one
        # measure disagreed with another. Refusing here is the cheap way to keep the
        # two in step without a second notion of what a paragraph is.
        if salutation and not is_salutation(salutation):
            return (
                paragraphs,
                f"Die Anrede \"{salutation[:40]}\" ist als Anrede nicht zu erkennen und "
                "zaehlt damit als Satz. Beginne sie mit 'Hallo', 'Guten Tag' oder "
                "'Sehr geehrte' - oder lass sie weg.",
                "rahmen",
            )
        if closing and not is_closing(closing):
            return (
                paragraphs,
                f"Die Grussformel \"{closing[:40]}\" ist als Grussformel nicht zu "
                "erkennen und zaehlt damit als Satz. Beginne sie mit 'Viele Gruesse' - "
                "oder lass sie weg.",
                "rahmen",
            )
        return paragraphs, "", ""

    def _read_entry(
        self, entry: object, index: int, known: dict[str, Fact], unit: str
    ) -> Paragraph:
        """One entry of either shape: text plus the ids it cites, both required."""
        if not isinstance(entry, dict):
            raise DraftError(f"{unit} {index} ist kein Objekt.")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise DraftError(f"{unit} {index} hat keinen Text.")
        raw_ids = entry.get("fact_ids", [])
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not all(isinstance(i, str) for i in raw_ids):
            raise DraftError(f"{unit} {index} hat keine auswertbare Liste von Kennungen.")
        unknown = [i for i in raw_ids if i not in known]
        if unknown:
            raise DraftError(
                f"{unit} {index} beruft sich auf unbekannte Kennungen: {', '.join(unknown)}."
            )
        return Paragraph(text=text.strip(), fact_ids=tuple(dict.fromkeys(raw_ids)))


def _frame_text(value: object) -> str:
    """A salutation or sign-off the model may or may not have written."""
    return value.strip() if isinstance(value, str) and value.strip() else ""
