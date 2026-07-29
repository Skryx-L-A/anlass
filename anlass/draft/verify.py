"""Stage 7: hold the finished text against the fact base. Separate pass.

Why this is not a pure model check
----------------------------------

A model asked "is this text covered by these facts?" answers wrongly exactly when it
matters: when it produced the text itself, when the fabrication is fluent, and when it
is confident. Asking a second model helps, but it is still the same failure mode with
a different seed, and it costs a call that may be unavailable.

So verification has two layers and they combine by union:

* A **deterministic floor** that cannot be talked out of anything. It extracts the
  checkable surface of the text - numbers, entity-shaped tokens, marketing absolutes,
  literal quotes - and demands that each of them occur in the grounding of the
  paragraph that used it. This is high precision, not high recall: it catches every
  invented number and every invented name that looks like a name, and it never
  depends on a model being honest.
* An optional **model layer** that reads whole sentences and catches paraphrased
  fabrication the floor cannot see ("wir haben das Projekt geleitet" where the facts
  only say "mitgearbeitet"). It may only *add* findings. A model saying "all fine"
  never clears a deterministic finding.

What the floor deliberately does not do
---------------------------------------

It does not flag every capitalised German word. In German every noun is capitalised,
so that rule would fire on ordinary prose and drown the real findings - and a check
nobody reads is not a check. The same applies to the weaker variant of treating two
adjacent capitalised words as a name: it was built, it fired on "im Alltag Arbeit
abnimmt", and it was removed. Therefore an invented name of ordinary German word shape
("Nordwind") passes the floor and is left to the model layer. Names with a shape that
common nouns do not have (digits, internal capitals, all caps, a legal form, a person
title, a domain) are caught. This gap is known, measured in the tests, and documented
rather than papered over.

Grounding of a paragraph
------------------------

The facts the paragraph *cites*, plus the lead's field values, plus the signal quote,
plus explicitly declared extra terms (the sender's own name, for instance). Lead and
signal are legitimate: their values come from the source and carry provenance.

If something is covered by a fact the paragraph did *not* cite, that is a WARNING
(``UNCITED_SUPPORT``), not an ERROR: the statement is true and grounded, only the
citation is incomplete. Blocking on it would produce false alarms without protecting
against fabrication - and false alarms are as expensive as misses here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from ..interfaces import LLM
from ..models import (
    Draft,
    Fact,
    Finding,
    FindingKind,
    Lead,
    Severity,
    Signal,
    VerificationResult,
)
from ._parse import extract_json

__all__ = ["GroundingVerifier", "DEFAULT_SUPERLATIVES"]


# --------------------------------------------------------------------------- lexicons

#: Marketing absolutes that are a factual claim in disguise. Only flagged when they do
#: not occur in the grounding, so a fact base that says "weltweit" may say it too.
DEFAULT_SUPERLATIVES = frozenset(
    {
        "schnellste", "schnellsten", "schnellster", "schnellstes",
        "groesste", "groessten", "groesster", "groesstes",
        "größte", "größten", "größter", "größtes",
        "fuehrend", "fuehrende", "fuehrenden", "fuehrender", "fuehrendes",
        "führend", "führende", "führenden", "führender", "führendes",
        "marktfuehrer", "marktführer",
        "branchenfuehrend", "branchenführend",
        "weltweit", "weltweite", "weltweiten", "weltweiter", "weltweites",
        "einzigartig", "einzigartige", "einzigartigen", "einzigartiger", "einzigartiges",
        "unschlagbar", "unschlagbare", "unschlagbaren",
        "revolutionaer", "revolutionär", "revolutionaere", "revolutionäre",
        "garantiert", "garantierte", "garantierten",
        "ausnahmslos", "perfekt", "perfekte", "perfekten", "perfekter", "perfektes",
        "wichtigste", "wichtigsten", "wichtigster", "wichtigstes",
    }
)

#: Number words worth expanding. "ein"/"eine" are left out on purpose - they are the
#: German indefinite article and would fire on every second sentence.
_NUMBER_WORDS = {
    "zwei": "2", "drei": "3", "vier": "4", "fuenf": "5", "fünf": "5", "sechs": "6",
    "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11",
    "zwoelf": "12", "zwölf": "12",
}


# ----------------------------------------------------------------------- extraction

# Deliberately without blanks in the character class: a class that spans blanks would
# swallow "7,5 auf 3,8" into one unparsable token and report a number nobody wrote.
_NUMBER_RE = re.compile(r"(?<![\w.,])\d[\d.,]*\d|(?<![\w.,])\d")

#: File extensions that must not be read as a top-level domain. Kept short on purpose:
#: every entry here is a hole in the domain check, so it only lists suffixes that occur
#: in ordinary prose as a file name ("Datei.pdf", "tests.yml"). Ambiguous ones that are
#: also realistic domains (``.sh``, ``.io``, ``.co``) are deliberately absent - a false
#: alarm on a file name is cheap, a fabricated domain slipping through is not.
_FILE_SUFFIXES = (
    "pdf|md|txt|py|yml|yaml|json|csv|xml|html|htm|png|jpg|jpeg|gif|svg|webp|"
    "zip|gz|tar|log|ini|cfg|conf|toml|lock|sql|docx|xlsx|pptx|odt|bak|tmp"
)

# A bare domain is recognised by the *shape* of its last label, not by a list of
# endings. The list this replaced knew eleven suffixes, so every invented domain
# outside them - .xyz, .example, .tech, .agency, .digital, .gmbh - walked past the
# deterministic floor, and those are exactly the endings of the companies this tool
# writes to. Measured in both directions in tests/test_poisoned_facts.py.
_URL_RE = re.compile(
    r"https?://[^\s,;)\"]+"
    r"|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"
    rf"|\b(?:[\w-]+\.)+(?!(?:{_FILE_SUFFIXES})\b)[A-Za-z]{{2,24}}\b",
    re.IGNORECASE,
)
_WITH_DIGIT_RE = re.compile(r"\b[A-Za-zÄÖÜäöüß]+-?\d+(?:[.\-]\w+)*\b")
_ACRONYM_RE = re.compile(r"\b[A-ZÄÖÜ]{2,}(?:[-/][A-ZÄÖÜ0-9]{1,})*\b")
_MIXEDCASE_RE = re.compile(r"\b[A-Za-zÄÖÜäöüß]*[a-zäöüß][A-ZÄÖÜ][A-Za-zÄÖÜäöüß]*\b")
_PERSON_RE = re.compile(r"\b(?:Herrn?|Frau|Dr\.|Prof\.)\s+((?:[A-ZÄÖÜ][\wäöüß'’-]+\s*){1,3})")
_LEGAL_RE = re.compile(
    r"((?:[A-ZÄÖÜ][\wäöüß&.'’-]*\s+){0,3}[A-ZÄÖÜ][\wäöüß&.'’-]*)\s+"
    r"(?:GmbH|AG|SE|KG|mbH|UG|Inc\.?|Ltd\.?|LLC|e\.\s?V\.)"
)
_QUOTE_RE = re.compile(r"[„\"]([^„\"“”]{8,200})[\"“”]")
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß'’-]*")


def _canonical_number(raw: str) -> str:
    """Canonical form of a number token, German and English notation alike.

    ``3,8`` and ``3.8`` become ``3.8``; ``5.000`` and ``5000`` become ``5000``.
    Version-like tokens (more than one dot) are returned as written and compared as
    strings. A blank as thousands separator is not joined - the tokenizer splits there,
    and both sides of the comparison split the same way.

    A spelled-out number resolves to its digits, so "zehn" and "10" compare equal. That
    used to happen on the grounding side only, which is how "zehn Jahre Berufserfahrung"
    reached a draft unchecked.
    """
    word = _NUMBER_WORDS.get(raw.strip().lower())
    if word is not None:
        return word
    text = raw.strip().replace(" ", "").replace(" ", "").rstrip(".,")
    if not text:
        return ""
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        text = text.replace(".", "")
        text = text.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", text) else text.replace(",", ".")
    elif has_dot:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
            text = text.replace(".", "")
        elif text.count(".") > 1:
            return raw.strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return raw.strip()
    normalized = value.normalize()
    return format(normalized, "f")


def _number_surfaces(text: str) -> list[str]:
    """Every number as it is written in ``text``: digits and spelled-out words alike.

    Kept apart from :func:`_numbers_in`, which returns canonical values for the
    grounding side. A check needs the surface, because the finding quotes it back to
    the reader - "die Zahl 'zehn'" is readable, "die Zahl '10'" sends them looking for
    a digit that is not there.
    """
    found: list[str] = [match.group(0).strip() for match in _NUMBER_RE.finditer(text)]
    for word in _NUMBER_WORDS:
        for match in re.finditer(rf"\b{word}\b", text, re.IGNORECASE):
            found.append(match.group(0))
    return found


def _numbers_in(text: str) -> set[str]:
    """Every number in ``text``, canonical, including spelled-out ones."""
    found = {_canonical_number(m.group(0)) for m in _NUMBER_RE.finditer(text)}
    lowered = text.lower()
    for word, digits in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            found.add(digits)
    found.discard("")
    return found


# A rule that treated two adjacent capitalised words mid-sentence as a name was built
# and then removed: it fired on "im Alltag Arbeit abnimmt", which is ordinary German,
# not a company. In a language that capitalises every noun, that shape carries no
# signal. Multi-word names now reach the floor only through a legal form or a person
# title; anything else is the model layer's job.


def _entities_in(text: str) -> list[str]:
    """Entity-shaped surfaces, in a stable order, without duplicates."""
    found: list[str] = []
    seen: set[str] = set()

    def add(surface: str) -> None:
        cleaned = " ".join(surface.split()).strip(" .,;:")
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            found.append(cleaned)

    for match in _URL_RE.finditer(text):
        add(match.group(0))
    for match in _PERSON_RE.finditer(text):
        add(match.group(1))
    for match in _LEGAL_RE.finditer(text):
        add(match.group(1))
    for pattern in (_WITH_DIGIT_RE, _ACRONYM_RE, _MIXEDCASE_RE):
        for match in pattern.finditer(text):
            add(match.group(0))
    return found


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


# ------------------------------------------------------------------------- grounding


@dataclass(frozen=True, slots=True)
class _Grounding:
    """Everything a paragraph is allowed to draw on, in comparable form."""

    text: str
    numbers: frozenset[str]

    @classmethod
    def of(cls, parts: Iterable[str]) -> "_Grounding":
        joined = "\n".join(part for part in parts if part)
        return cls(text=_normalize(joined), numbers=frozenset(_numbers_in(joined)))

    def covers_text(self, surface: str) -> bool:
        return _normalize(surface) in self.text

    def covers_number(self, canonical: str) -> bool:
        return canonical in self.numbers


#: Longest field value that may still count as grounding. Above this a field is bulk
#: text, not an identifying detail.
MAX_GROUNDING_FIELD_CHARS = 400


def _lead_parts(lead: Lead | None, include_text: bool) -> list[str]:
    """Grounding contributed by the lead: short, identifying field values only.

    ``include_text`` keeps the raw source text out by default, because a long text
    contains almost any token and would soften the check considerably. The same
    softening returns through the back door if a *field* holds bulk text - and one
    does: a page fetched in stage 3 lands in ``page_text`` at eighteen thousand
    characters of navigation, cookie notice and, on a job board, dozens of unrelated
    company names. Measured on 2026-07-29: a fabricated former employer taken from
    that soup was refused without the lead and accepted with it.

    So length decides, not the field's name. An organisation, a location, a contact
    name are identifying details and ground a sentence; a scraped page is a haystack
    and grounds nothing. Evidence strings stay regardless - they are short quotes an
    enricher recorded on purpose.
    """
    if lead is None:
        return []
    parts = [
        value
        for value in (str(entry.value) for entry in lead.fields.values())
        if len(value) <= MAX_GROUNDING_FIELD_CHARS
    ]
    parts.extend(entry.evidence for entry in lead.fields.values() if entry.evidence)
    if include_text and lead.text:
        parts.append(lead.text)
    return parts


# -------------------------------------------------------------------------- verifier

_MODEL_INSTRUCTIONS = """Pruefe einen fertigen Text gegen eine Faktenliste.

Deine einzige Aufgabe: finde jede Tatsachenaussage im TEXT, die nicht durch die
FAKTENLISTE, die EMPFAENGERDATEN oder das ANLASS-ZITAT gedeckt ist. Dazu gehoeren
auch Verschiebungen im Grad (aus Mitarbeit wird Leitung), erfundene Zeitraeume und
Steigerungen. Hoefliche Floskeln, Absichtsbekundungen und Fragen sind keine
Tatsachenaussagen.

Antworte ausschliesslich mit JSON in dieser Form:
{"unsupported": [{"quote": "woertliche Stelle aus dem Text", "reason": "kurz warum"}]}

Ist alles gedeckt, antworte mit {"unsupported": []}."""


@dataclass
class GroundingVerifier:
    """The verification pass. Deterministic floor plus optional model layer.

    Args:
        llm: Model for the second layer. ``None`` runs the floor alone, which is a
            fully valid mode and the one that works offline.
        extra_grounding: Terms that are legitimate without being in the fact base -
            the sender's own name and signature, for example.
        include_lead_text: Whether the lead's raw source text counts as grounding.
            Off by default: a long raw text contains almost any token and would soften
            the check considerably.
        superlatives: Overridable lexicon of marketing absolutes.
        min_words_for_citation: A paragraph longer than this without a single cited
            fact id produces a WARNING.
    """

    llm: LLM | None = None
    extra_grounding: Sequence[str] = field(default_factory=tuple)
    include_lead_text: bool = False
    superlatives: frozenset[str] = DEFAULT_SUPERLATIVES
    min_words_for_citation: int = 25

    @property
    def name(self) -> str:
        return "grounding" if self.llm is None else f"grounding+{self.llm.name}"

    def verify(
        self,
        draft: Draft,
        facts: Sequence[Fact],
        *,
        lead: Lead | None = None,
        signal: Signal | None = None,
    ) -> VerificationResult:
        findings = list(self._deterministic(draft, facts, lead, signal))
        checkers = ["deterministic"]
        if self.llm is not None:
            checkers.append(f"model:{self.llm.name}")
            findings.extend(self._model_layer(draft, facts, lead, signal))
        return VerificationResult(draft_id=draft.id, findings=tuple(findings), checkers=tuple(checkers))

    # ----------------------------------------------------------------- layer 1

    def _deterministic(
        self,
        draft: Draft,
        facts: Sequence[Fact],
        lead: Lead | None,
        signal: Signal | None,
    ) -> list[Finding]:
        known = {fact.id: fact for fact in facts}
        shared = [*_lead_parts(lead, self.include_lead_text), *self.extra_grounding]
        if signal is not None:
            shared.append(signal.quote)
        everything = _Grounding.of([*shared, *(f"{f.claim} {f.source}" for f in facts)])

        findings: list[Finding] = []
        seen: set[tuple[str, str, int]] = set()

        def report(kind: FindingKind, severity: Severity, message: str, excerpt: str, index: int) -> None:
            key = (kind.value, excerpt.lower(), index)
            if key in seen:
                return
            seen.add(key)
            findings.append(
                Finding(
                    kind=kind,
                    severity=severity,
                    message=message,
                    excerpt=excerpt,
                    paragraph=index,
                    checker="deterministic",
                )
            )

        for index, paragraph in enumerate(draft.paragraphs):
            unknown = [fid for fid in paragraph.fact_ids if fid not in known]
            for fact_id in unknown:
                report(
                    FindingKind.UNKNOWN_FACT_ID,
                    Severity.ERROR,
                    f"Absatz {index + 1} beruft sich auf die Kennung '{fact_id}', "
                    "die es in der Faktenbasis nicht gibt.",
                    fact_id,
                    index,
                )

            cited = [known[fid] for fid in paragraph.fact_ids if fid in known]
            grounding = _Grounding.of([*shared, *(f"{f.claim} {f.source}" for f in cited)])
            text = paragraph.text

            if not paragraph.fact_ids and len(text.split()) > self.min_words_for_citation:
                report(
                    FindingKind.UNCITED_PARAGRAPH,
                    Severity.WARNING,
                    f"Absatz {index + 1} ist laenger als {self.min_words_for_citation} Woerter "
                    "und nennt keinen einzigen Beleg.",
                    text[:80],
                    index,
                )

            # Digits AND spelled-out numbers. The two used to part ways here: the
            # grounding side went through _numbers_in, which knows the number words,
            # while this loop matched _NUMBER_RE, which only ever sees digits. So a
            # fabricated quantity written as a word was never checked at all -
            # "zehn Jahre Berufserfahrung bei Google" passed with no finding, which is
            # exactly the shape a lie in an application takes. Measured 29.07.2026
            # against a poisoned posting that asked the model for that very sentence.
            for surface in _number_surfaces(text):
                canonical = _canonical_number(surface)
                if not canonical or grounding.covers_number(canonical):
                    continue
                if everything.covers_number(canonical):
                    report(
                        FindingKind.UNCITED_SUPPORT,
                        Severity.WARNING,
                        f"Die Zahl '{surface}' in Absatz {index + 1} steht in der Faktenbasis, "
                        "aber der Absatz nennt den zugehoerigen Beleg nicht.",
                        surface,
                        index,
                    )
                else:
                    report(
                        FindingKind.UNSUPPORTED_NUMBER,
                        Severity.ERROR,
                        f"Die Zahl '{surface}' in Absatz {index + 1} steht in keinem Beleg.",
                        surface,
                        index,
                    )

            for surface in _entities_in(text):
                if grounding.covers_text(surface):
                    continue
                if everything.covers_text(surface):
                    report(
                        FindingKind.UNCITED_SUPPORT,
                        Severity.WARNING,
                        f"'{surface}' in Absatz {index + 1} steht in der Faktenbasis, aber der "
                        "Absatz nennt den zugehoerigen Beleg nicht.",
                        surface,
                        index,
                    )
                else:
                    report(
                        FindingKind.UNSUPPORTED_ENTITY,
                        Severity.ERROR,
                        f"'{surface}' in Absatz {index + 1} kommt in keinem Beleg vor.",
                        surface,
                        index,
                    )

            for word in _WORD_RE.findall(text):
                if word.lower() not in self.superlatives or grounding.covers_text(word):
                    continue
                report(
                    FindingKind.UNSUPPORTED_SUPERLATIVE,
                    Severity.ERROR,
                    f"'{word}' in Absatz {index + 1} ist eine Steigerung, die kein Beleg deckt.",
                    word,
                    index,
                )

            for match in _QUOTE_RE.finditer(text):
                quoted = match.group(1).strip()
                if not grounding.covers_text(quoted):
                    report(
                        FindingKind.UNSUPPORTED_QUOTE,
                        Severity.ERROR,
                        f"Das Zitat in Absatz {index + 1} steht so in keinem Beleg.",
                        quoted[:120],
                        index,
                    )

        return findings

    # ----------------------------------------------------------------- layer 2

    def _model_layer(
        self,
        draft: Draft,
        facts: Sequence[Fact],
        lead: Lead | None,
        signal: Signal | None,
    ) -> list[Finding]:
        assert self.llm is not None
        checker = f"model:{self.llm.name}"
        prompt = self._model_prompt(draft, facts, lead, signal)
        try:
            # 4000, not 1200: measured on 2026-07-29 against a local reasoning model,
            # 1200 held the format in 12/15 calls and 4000 in 10/10. The three failures
            # were all the same case - the fully grounded paragraph, the one the model
            # deliberates over longest - and its whole budget went to the reasoning step.
            raw = self.llm.complete(prompt, temperature=0.0, max_tokens=4000)
        except Exception as exc:  # provider down, timeout, refusal
            return [
                Finding(
                    kind=FindingKind.MODEL_UNAVAILABLE,
                    severity=Severity.WARNING,
                    message=f"Die Modellpruefung lief nicht: {exc}. Es wurde nur maschinell geprueft.",
                    checker=checker,
                )
            ]
        data = extract_json(raw)
        if data is None or not isinstance(data.get("unsupported"), list):
            return [
                Finding(
                    kind=FindingKind.MODEL_UNAVAILABLE,
                    severity=Severity.WARNING,
                    message="Die Modellpruefung lieferte keine auswertbare Antwort. "
                    "Es wurde nur maschinell geprueft.",
                    checker=checker,
                )
            ]
        findings: list[Finding] = []
        for entry in data["unsupported"]:
            if not isinstance(entry, dict):
                continue
            quote = str(entry.get("quote", "")).strip()
            reason = str(entry.get("reason", "")).strip()
            if not quote:
                continue
            findings.append(
                Finding(
                    kind=FindingKind.MODEL_FLAGGED,
                    severity=Severity.ERROR,
                    message=f"Unbelegte Aussage laut Modellpruefung: {reason or 'ohne Begruendung'}.",
                    excerpt=quote[:200],
                    paragraph=self._locate(draft, quote),
                    checker=checker,
                )
            )
        return findings

    def _model_prompt(
        self,
        draft: Draft,
        facts: Sequence[Fact],
        lead: Lead | None,
        signal: Signal | None,
    ) -> str:
        blocks = [_MODEL_INSTRUCTIONS]
        blocks.append(
            "FAKTENLISTE:\n"
            + "\n".join(f"[{f.id}] {f.claim} (Beleg: {f.source})" for f in facts)
        )
        if lead is not None:
            blocks.append(
                "EMPFAENGERDATEN:\n"
                + "\n".join(f"{k}: {v.value}" for k, v in sorted(lead.fields.items()))
            )
        if signal is not None:
            blocks.append(f'ANLASS-ZITAT:\n"{signal.quote}"')
        if self.extra_grounding:
            blocks.append("ZUSAETZLICH ERLAUBT:\n" + "\n".join(self.extra_grounding))
        blocks.append(
            "TEXT:\n"
            + "\n\n".join(
                f"[Absatz {i + 1}] {p.text}" for i, p in enumerate(draft.paragraphs)
            )
        )
        return "\n\n".join(blocks)

    @staticmethod
    def _locate(draft: Draft, quote: str) -> int | None:
        needle = _normalize(quote)
        for index, paragraph in enumerate(draft.paragraphs):
            if needle and needle in _normalize(paragraph.text):
                return index
        return None
