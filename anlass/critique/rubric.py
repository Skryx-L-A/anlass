"""The second measure: is the draft good. Stage 7 only ever asked whether it is true.

Why this exists at all
----------------------

The verification pass answers exactly one question: does the text claim something the
fact base does not carry. Both drafts of the first real run passed it, and both were
poor applications - wrong form, wrong salutation, a paragraph that reads as a catalogue,
no delivered product named. None of that is a fabrication, so none of it was measurable
(Q8). This module is the measure that was missing.

How a criterion is built
------------------------

Each criterion has a name, a weight and a check. The check answers whether it holds and
gives **a German sentence that says what to do** - not what was wrong. That sentence goes
back to the generator verbatim as a revision note, which is why it is phrased as an
instruction: "Schreibe die Anrede in Du-Form, die Ausschreibung duzt" is actionable,
"Anrede falsch" is not.

Points are all or nothing per criterion. Partial credit would change no decision - a
draft is revised unless it reaches every point - and it would make the notes harder to
read, because half a criterion has no clear instruction attached to it. The weight is
what makes one open criterion weigh more than another when the best of several attempts
has to be picked.

What is deterministic and what is not
-------------------------------------

Everything that can be counted is counted here: sentences, evidence per paragraph,
salutation forms, mixed spelling, length, a lexicon of application-German. What needs
judgement (does this paragraph really carry one thought, is the occasion quoted or
merely alluded to) is left to the optional model layer in :mod:`anlass.critique.critic`
- and that layer may only take points away, never grant them. If the model is not
reachable, the deterministic points stand and the run says so. A quality measure that
stops working without a network is not a measure.

An empty field is not a failure. If the posting demanded no form, ``form_eingehalten``
gives full points: a criterion must never punish a draft for something nobody asked for.
"""

from __future__ import annotations

import re
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from ..errors import ConfigError
from ..models import Draft, Fact, Lead, Signal, VerificationResult
from ._text import (
    body_sentence_count,
    content_words,
    fold,
    is_closing,
    is_salutation,
    normalize,
    sentences,
    words,
)

__all__ = [
    "Criterion",
    "CriterionResult",
    "DEFAULT_DELIVERY_MARKERS",
    "DEFAULT_FILLERS",
    "DEFAULT_MEASUREMENT_MARKERS",
    "DEFAULT_OFFERS",
    "DEFAULT_TRANSLITERATIONS",
    "DraftContext",
    "Judgement",
    "RUBRIC_FILE",
    "Rubric",
    "STRENGTH_MARKERS",
    "Settings",
    "default_rubric",
    "is_strength",
    "load_rubric",
    "rubric_for_profile",
]

#: Where a user's own rubric lives, next to ``facts.yaml`` in the profile directory.
RUBRIC_FILE = "rubric.yaml"

# The field names stage 3 extracts from the posting. They are string literals here
# because :mod:`anlass.models` belongs to another part of this phase; when the
# constants land there, these are the same names.
_FORMAT = "application_format"
_MAX_SENTENCES = "max_sentences"
_REQUIRED = "required_artifacts"
_FORBIDDEN = "forbidden_artifacts"
_ADDRESS = "form_of_address"
_CONTACT = "contact_name"


# --------------------------------------------------------------------------- lexicons

#: Application German. Phrases, not single words, wherever a single word would fire on
#: ordinary prose. Every entry is written folded, because that is what it is compared
#: against.
DEFAULT_FILLERS = frozenset(
    {
        "hiermit bewerbe ich mich",
        "mit grossem interesse",
        "ich wuerde mich freuen",
        "wuerde mich sehr freuen",
        "ich koennte mir vorstellen",
        "haette ich interesse",
        "wuerde gerne",
        "haette gerne",
        "spannende herausforderung",
        "neue herausforderung",
        "reizvolle aufgabe",
        "teamplayer",
        "teamfaehig",
        "belastbar",
        "hochmotiviert",
        "hoch motiviert",
        "leidenschaftlich",
        "mit leidenschaft",
        "wie sie meinem lebenslauf entnehmen",
        "ich bin der richtige",
        "runden mein profil ab",
        "verfuege ueber",
        "im rahmen meiner taetigkeit",
        "freue ich mich auf ihre antwort",
        "freue ich mich auf ihre rueckmeldung",
        "moeglicherweise",
        "eventuell",
        "hervorragend",
        "exzellent",
        "erstklassig",
        "einzigartig",
        "perfekt",
    }
)

#: Words that betray ASCII transliteration. Used only to detect *mixed* spelling, so a
#: fact base written entirely without umlauts stays unflagged - that is consistent, and
#: consistency is what the criterion measures. A lexicon rather than a pattern on
#: purpose: "neue", "Quelle" and "Feuer" all carry the letter pairs and none of them is
#: a transliteration.
DEFAULT_TRANSLITERATIONS = frozenset(
    {
        "fuer", "ueber", "moeglich", "moeglichkeit", "koennen", "koennte", "waere",
        "muessen", "muesste", "laeuft", "laufen", "gruesse", "gruessen", "gespraech",
        "gespraeche", "loesung", "loesungen", "hoeren", "veroeffentlicht",
        "veroeffentlichung", "oeffentlich", "oeffentliche", "oeffentlichen", "groesse",
        "groesser", "schliesslich", "massnahme", "massnahmen", "strasse", "haette",
        "regelmaessig", "gemaess", "naechste", "naechsten", "spaeter", "zunaechst",
        "taetigkeit", "erklaeren", "verstaendlich", "unterstuetzung", "zurueck",
        "natuerlich", "persoenlich", "waehrend", "beitraege", "erfahrungsgemaess",
    }
)

#: Marks a fact as something finished rather than as a way of working. Finding Q5: the
#: Nordlicht draft described only the workbench and named no built product, although
#: the posting asked for exactly that.
DEFAULT_DELIVERY_MARKERS = frozenset(
    {
        "veroeffentlicht", "release", "version", "fassung", "laeuft auf", "im einsatz",
        "ausgeliefert", "verfuegbar", "installiert", "download", "paket", "tap",
        "gebaut", "umgebaut", "fertiggestellt", "eingefuehrt", "in betrieb",
        "produktiv", "github.com", "gitlab.com",
    }
)

#: Marks a fact as something *measured* rather than merely asserted. Kept apart from
#: the delivery markers above because the two mean different things: the criterion
#: ``ausgeliefertes_genannt`` asks for a finished, visible result, and a measurement is
#: not a delivery. Together they are what :func:`is_strength` reads.
DEFAULT_MEASUREMENT_MARKERS = frozenset(
    {"gemessen", "gemessene", "gemessenen", "messung", "messungen", "nachgemessen", "benchmark"}
)

#: What tells a *strength* from mere eligibility: something built, delivered, published
#: or measured. See :func:`is_strength` for what that distinction is for.
STRENGTH_MARKERS = DEFAULT_DELIVERY_MARKERS | DEFAULT_MEASUREMENT_MARKERS

#: A concrete closing. A question mark counts on its own; these are the offers that
#: work without one. Deliberately narrow - "ich freue mich auf ein Gespraech" is a
#: phrase, not an offer, and it is in the filler lexicon above.
DEFAULT_OFFERS = frozenset(
    {
        "ich schicke", "ich sende", "ich zeige", "ich rufe", "ich melde mich am",
        "hier ist der link", "der code liegt", "ich bringe", "ich stelle es vor",
        "ich komme vorbei",
    }
)


# ----------------------------------------------------------------------------- types


@dataclass(frozen=True, slots=True)
class Settings:
    """The numbers and lexicons the criteria read. Overridable from ``rubric.yaml``."""

    fillers: frozenset[str] = DEFAULT_FILLERS
    transliterations: frozenset[str] = DEFAULT_TRANSLITERATIONS
    delivery_markers: frozenset[str] = DEFAULT_DELIVERY_MARKERS
    offers: frozenset[str] = DEFAULT_OFFERS
    max_facts_per_paragraph: int = 3
    min_words_for_citation: int = 15
    min_words: int = 60
    max_words: int = 320
    words_per_sentence: int = 25
    min_occasion_overlap: float = 0.34
    """How much of the occasion a **letter** has to pick up verbatim.

    There is deliberately no counterpart for a short form: see :func:`_check_occasion`
    for why a lower number there would have been fitted to a single draft.
    """


@dataclass(frozen=True, slots=True)
class DraftContext:
    """Everything a criterion may look at. Read-only, and nothing is fetched here."""

    draft: Draft
    lead: Lead | None = None
    signal: Signal | None = None
    facts: Sequence[Fact] = ()
    verification: VerificationResult | None = None

    @property
    def text(self) -> str:
        return self.draft.text

    def field(self, name: str, default: Any = None) -> Any:
        """A field of the lead, or ``default``. A missing lead is not an error."""
        return default if self.lead is None else self.lead.value(name, default)

    def field_list(self, name: str) -> list[str]:
        """A field that may hold one entry or several, always as a list of strings."""
        raw = self.field(name)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw.strip()] if raw.strip() else []
        if isinstance(raw, AbcSequence):
            return [str(entry).strip() for entry in raw if str(entry).strip()]
        return [str(raw).strip()]

    @property
    def body_paragraphs(self) -> list[tuple[int, str]]:
        """Paragraphs that say something, with their number. Salutation and sign-off
        carry no statement, so every criterion that asks for evidence skips them."""
        return [
            (index, paragraph.text)
            for index, paragraph in enumerate(self.draft.paragraphs, start=1)
            if not is_salutation(paragraph.text) and not is_closing(paragraph.text)
        ]


@dataclass(frozen=True, slots=True)
class Judgement:
    """What a check returns: whether it holds, and the sentence that says what to do.

    A check does not know its own weight. It answers yes or no; how much that is worth
    is the rubric's decision, and it lives in the configurable weight.
    """

    passed: bool
    note: str = ""
    applicable: bool = True
    """``False`` when the criterion cannot apply to this draft at all.

    Not the same as failing. A three-sentence answer to "describe what you built" has
    no salutation and no closing, so asking for either is not a defect to be revised -
    it is a question that does not arise. Scored as a failure it would put points
    permanently out of reach, and a rubric with unreachable points can never be
    answered by revising, only by giving up. A criterion that does not apply is left
    out of BOTH sums, so full marks stay attainable.
    """


@dataclass(frozen=True, slots=True)
class Criterion:
    """One named property of a draft, with its weight and its check.

    Args:
        name: Stable identifier, also the key in ``rubric.yaml``.
        weight: Points this criterion is worth. ``0`` switches it off.
        purpose: One German line. Shown to the user and handed to the model layer.
        check: The deterministic test.
        fallback_note: The revision note used when the model layer flags this criterion
            without a usable sentence of its own.
        model_checkable: Whether the model layer may take this criterion's points away.
            Off for everything a machine can count exactly - a model must not overrule
            a sentence count.
    """

    name: str
    weight: int
    purpose: str
    check: Callable[[DraftContext, Settings], Judgement]
    fallback_note: str = ""
    model_checkable: bool = False


@dataclass(frozen=True, slots=True)
class CriterionResult:
    """What one criterion made of one draft."""

    name: str
    weight: int
    earned: int
    possible: int
    note: str = ""
    checker: str = "deterministic"

    @property
    def full(self) -> bool:
        return self.earned >= self.possible


# ------------------------------------------------------------------------ the checks


@lru_cache(maxsize=512)
def _marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(marker)}\b")


def _marker_hits(haystack: str, markers: frozenset[str]) -> list[str]:
    """Which markers occur in ``haystack``, sorted, matched on word boundaries.

    Boundaries and not substrings: 'gebaut' otherwise fires inside 'umgebaut' and
    'perfekt' inside 'perfektioniert', which turns a lexicon into a random generator.
    ``haystack`` has to be normalised, the markers are stored folded.
    """
    return sorted(marker for marker in markers if _marker_pattern(marker).search(haystack))


def is_strength(fact: Fact) -> bool:
    """Whether this fact names a **strength** rather than mere eligibility.

    Two sorts of fact live in a fact base, and they are not worth the same as an
    occasion to write (phase 6, task 1):

    * A **strength** is something built, delivered, published or measured. It answers
      "why this applicant".
    * **Eligibility** is a language, a degree, a place of residence. It answers only
      "may this applicant", which every other applicant answers too.

    Measured on 2026-07-29 against the real fact base: the sharpest occasion Nordlicht
    offered ("Du hast nachweislich schon etwas mit KI gebaut oder automatisiert") lost
    the ranking to "Verhandlungssicheres Deutsch, sicheres Englisch", because the
    language sentence happened to overlap a language fact more literally. Full marks
    followed, and the letter opened with the one sentence about the applicant that says
    nothing about them. :mod:`anlass.signal.detect` reads this function to keep that
    from happening again.

    The test is a lexicon, deliberately one-sided: it can show that a fact **is** a
    strength, never that it is not one. "Ich baue an einem Uebersetzer" carries no
    marker and is still an achievement. So the answer is "proven strength" against
    "not proven", which is why the caller treats it as a bonus and not as a penalty on
    the rest - a fact base does not have to be annotated for the ranking to work, and
    an unrecognised strength costs its bonus, not its place.

    The lexicon lives here because :data:`DEFAULT_DELIVERY_MARKERS` and the
    word-boundary matching it needs are already here. One lexicon in one file, so the
    two readers cannot drift apart.
    """
    return bool(_marker_hits(normalize(f"{fact.claim} {fact.source}"), STRENGTH_MARKERS))


def _excerpt(text: str, *, limit: int = 60) -> str:
    """A quotable piece: long enough to find the place, short enough to read."""
    condensed = " ".join(text.split())
    return condensed if len(condensed) <= limit else condensed[:limit].rstrip() + " ..."


def _locate(index: int, text: str) -> str:
    """Name a paragraph by its number **and** its opening words.

    A note that says "Absatz 2" asks the reader to count paragraphs and to guess which
    one is meant, and a paragraph number stops being true the moment the text is
    rewritten - which is what a revision note asks for. Measured over the runs of
    29.07.2026: ``ein_gedanke_je_absatz`` stayed open through every round of the Talwerk
    draft while its note named nothing but a number (phase 6, finding 3). Quoting the
    text costs a line and points at something that exists.
    """
    return f"Absatz {index} (\"{_excerpt(text, limit=40)}\")"


def _sentence_count(ctx: DraftContext) -> int:
    """The one sentence count, shared with stage 6. See :func:`body_sentence_count`."""
    return body_sentence_count(paragraph.text for paragraph in ctx.draft.paragraphs)


def _mentions(text: str, term: str) -> bool:
    """Whether ``term`` is present in ``text``, by its content words.

    A required artifact is named as prose ("Link auf ein Repository"), so a literal
    substring test would miss it. The content words of the term have to occur; the
    short ones are ignored, which is what makes "ein Video" match "Video".
    """
    needle = content_words(term)
    if not needle:
        return fold(term) in normalize(text)
    haystack = content_words(text)
    return needle.issubset(haystack)


def _check_form(ctx: DraftContext, settings: Settings) -> Judgement:
    demanded = str(ctx.field(_FORMAT) or "").strip()
    forbidden = ctx.field_list(_FORBIDDEN)
    required = ctx.field_list(_REQUIRED)
    raw_max = ctx.field(_MAX_SENTENCES)
    if not demanded and not forbidden and not required and raw_max is None:
        return Judgement(True)

    problems: list[str] = []
    if raw_max is not None:
        try:
            limit = int(raw_max)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            count = _sentence_count(ctx)
            if count > limit:
                problems.append(
                    f"Kuerze den Entwurf auf hoechstens {limit} Saetze, die Ausschreibung "
                    f"verlangt genau das (es sind {count})"
                )
    for entry in forbidden:
        if _forbids_cover_letter(entry) and _looks_like_a_letter(ctx):
            problems.append(
                "Schreibe kein Anschreiben, die Ausschreibung schliesst es aus - liefere "
                "die verlangte Kurzform ohne Anrede und Grussformel"
            )
        elif not _forbids_cover_letter(entry) and _mentions(ctx.text, entry):
            problems.append(f"Nimm '{entry}' heraus, die Ausschreibung will das ausdruecklich nicht")
    for entry in required:
        if _describes_the_form(entry):
            continue
        if not _names_artifact(ctx.text, entry):
            problems.append(f"Nimm '{entry}' auf, die Ausschreibung verlangt es")
    if problems:
        return Judgement(False, "; ".join(problems) + ".")
    return Judgement(True)


#: Words with which a posting describes the *shape* of the answer rather than a thing to
#: attach. Deliberately only units of text - anything wider would swallow real demands.
#: "Seite" is deliberately absent: a page count is rare in these postings and the word
#: also means a website, which is a thing one can be asked to send.
_FORM_WORDS = frozenset({"satz", "saetze", "saetzen", "wort", "woerter", "worten",
                         "zeile", "zeilen", "absatz", "absaetze"})


def _describes_the_form(entry: str) -> bool:
    """Whether a "required artifact" is really the demanded form said twice.

    Measured on 29.07.2026 against the Nordlicht posting: stage 3 wrote
    "3 Saetze zu zuletzt mit KI gebautem/automatisiertem" into ``application_format``
    **and** into ``required_artifacts``. As an artifact that is unsatisfiable - the
    three sentences are the answer, not something to enclose with it - and it put five
    points permanently out of reach for a draft that followed the instruction exactly
    (17 of 26 instead of 26). The sentence bound itself is enforced above, from
    ``max_sentences``, so nothing is lost by skipping it here.

    The test is the unit of text, not "is it a substring of the format": Talwerk's
    posting states its artifact **inside** its format sentence ("GitHub-Link ... und
    zwei Saetze, warum genau diese Rolle"), and a subset test skipped the repository
    link along with it.
    """
    return bool(content_words(entry) & _FORM_WORDS)


#: The nouns of a demand, split at their components: "GitHub-Link" gives "github" and
#: "link", "Integrations-Projekt" gives two. Capitalised because that is how German
#: marks a noun, and the noun is the thing being demanded - "gebaut" and "kurzes" are not.
_NOUN_RE = re.compile(r"[A-ZÄÖÜ][\wÄÖÜäöüß]*(?:-[\wÄÖÜäöüß]+)*")


def _artifact_terms(entry: str) -> list[str]:
    """The words that name the demanded thing, ready to look for in a draft."""
    terms: list[str] = []
    for noun in _NOUN_RE.findall(entry):
        terms.extend(part for part in re.split(r"[^a-z0-9]+", fold(noun)) if len(part) >= 4)
    return terms


def _names_artifact(text: str, entry: str) -> bool:
    """Whether the draft names the demanded attachment - by its noun, not verbatim.

    An artifact is stated as prose: "GitHub-Link auf ein Automatisierungs- oder
    Integrations-Projekt, das du gebaut hast". Demanding every content word of that
    is unsatisfiable, and measured on 29.07.2026 it was: the Talwerk answer opens
    with the repository URL and matches **none** of the five, so the criterion could
    only be answered by giving up - the defect class of Q12 and Q21 a third time.

    What is looked for instead is the thing being demanded, and in German that is the
    capitalised word: "GitHub-Link", "Integrations-Projekt", "Video", "Lebenslauf" -
    not "gebaut", not "kurzes". Split at its components, so "GitHub-Link" is found in
    "github.com/beispiel/werkbank". **One** of them is enough: naming the thing
    once is what was asked for, and leniency is the safe direction here, because a
    criterion that withholds points for something the draft did do cannot be answered by
    revising at all.

    A demand with no noun in it falls back to the strict reading - there is nothing to
    point at, so the whole phrase has to be there.
    """
    terms = _artifact_terms(entry)
    if not terms:
        return _mentions(text, entry)
    haystack = normalize(text)
    return any(term in haystack for term in terms)


def _forbids_cover_letter(entry: str) -> bool:
    return "anschreiben" in fold(entry)


def _looks_like_a_letter(ctx: DraftContext) -> bool:
    """A salutation plus a sign-off is what makes a text a cover letter.

    Not the length: three sentences with "Sehr geehrte Damen und Herren" in front and
    "Viele Gruesse" behind them are a short cover letter, and Talwerk asked for none.
    """
    texts = [paragraph.text for paragraph in ctx.draft.paragraphs]
    return any(is_salutation(text) for text in texts) and any(is_closing(text) for text in texts)


#: The informal address in the singular. Used to find an informal **slip inside a
#: formal letter**, so every entry here has to be unambiguous once the text is folded
#: to lower case.
_DU_RE = re.compile(r"\b(du|dir|dich|dein|deine|deinem|deinen|deiner|deines|euch|euer)\b")

#: Evidence that a letter **is** written informally. Wider than the list above,
#: because a company is addressed in the plural: "ihr", "euch", "eure". Measured on
#: 29.07.2026: a draft that duzte correctly ("Woran wuerdet ihr mich zuerst
#: ransetzen?") lost all four points of the criterion and was told to write in the
#: Du-Form it was already written in - a note that asks for something already done is
#: the least followable note there is.
#:
#: This list must never be used the other way round. Folded to lower case, the polite
#: "Ihre" and the possessive "ihre" are the same string, so looking for an informal
#: slip with it would flag every correct formal letter.
_INFORMAL_RE = re.compile(
    r"\b(du|dir|dich|dein|deine|deinem|deinen|deiner|deines"
    r"|ihr|euch|euer|eure|eurem|euren|eurer|eures)\b"
)

# Case sensitive on purpose: lower case "sie" is "they", the polite form is capitalised.
_SIE_RE = re.compile(r"\b(Sie|Ihnen|Ihre|Ihrem|Ihren|Ihrer|Ihres|Ihr)\b")


def _is_short_form(ctx: DraftContext) -> bool:
    """True when the posting asked for a short answer rather than a letter.

    Two signals, either is enough: the posting rules a cover letter out, or it caps the
    answer at a handful of sentences. Both mean the same thing in practice - what is
    wanted is an answer, not correspondence.

    This matters because a short answer has no salutation and no sign-off. Scoring those
    as failures would put points permanently out of reach for exactly the postings whose
    instructions were followed most closely, and the revision loop would keep trying to
    add a greeting the recipient explicitly did not want (Q1 and Q12).
    """
    for entry in ctx.field_list(_FORBIDDEN):
        if _forbids_cover_letter(entry):
            return True
    raw_max = ctx.field(_MAX_SENTENCES)
    if raw_max is not None:
        try:
            return int(raw_max) <= 5
        except (TypeError, ValueError):
            return False
    return False


def _address_note(wanted: str, wrong: Sequence[str]) -> str:
    """"Write it in X" plus the words that have to go, where there are any."""
    note = f"Schreibe die Anrede in {wanted}"
    if wrong:
        note += " - ersetze " + ", ".join(f"'{word}'" for word in wrong[:3])
    return note


def _check_address(ctx: DraftContext, settings: Settings) -> Judgement:
    if _is_short_form(ctx):
        return Judgement(True, applicable=False)
    text = ctx.text
    wanted = fold(str(ctx.field(_ADDRESS) or "")).strip()
    name = str(ctx.field(_CONTACT) or "").strip()
    problems: list[str] = []

    # The words that are actually in the way are named, not just the form that is
    # wanted: "ersetze 'Ihre', 'Sie'" can be carried out and checked afterwards, "die
    # Anrede stimmt nicht" leaves the reader searching.
    if wanted.startswith("du"):
        wrong = sorted({match.group(0) for match in _SIE_RE.finditer(text)})
        if not _INFORMAL_RE.search(normalize(text)) or wrong:
            problems.append(_address_note("Du-Form, die Ausschreibung duzt", wrong))
    elif wanted.startswith("sie"):
        wrong = sorted({match.group(0) for match in _DU_RE.finditer(normalize(text))})
        if not _SIE_RE.search(text) or wrong:
            problems.append(_address_note("Sie-Form, die Ausschreibung siezt", wrong))
    if name and fold(name) not in normalize(text):
        problems.append(f"Rede {name} namentlich an, der Name steht in der Ausschreibung")
    if problems:
        return Judgement(False, "; ".join(problems) + ".")
    return Judgement(True)


def _check_occasion(ctx: DraftContext, settings: Settings) -> Judgement:
    if ctx.signal is None:
        return Judgement(
            False,
            "Greife einen zitierbaren Anlass aus der Ausschreibung auf; ohne ihn wird "
            "nicht angeschrieben.",
        )
    quote = ctx.signal.quote.strip()
    if ctx.lead is not None and ctx.lead.text and normalize(quote) not in normalize(ctx.lead.text):
        return Judgement(
            False,
            "Beziehe dich auf einen Satz, der wirklich in der Ausschreibung steht - der "
            "bisherige Anlass steht dort nicht.",
        )
    if _is_short_form(ctx):
        # Everything above stays: there has to be an occasion, and it has to stand in
        # the posting. What the floor gives up here is the last question - was it taken
        # up in the text - because in a short form the text IS the answer to it, and
        # whether an answer is on topic cannot be counted.
        #
        # Measured on 29.07.2026 against the Nordlicht answer, which names three
        # built projects with evidence and answers the question about as well as three
        # sentences can: it shares **one** of the occasion's eight content words
        # ("gebaut"). The rest are the question's own framing ("hast", "etwas",
        # "nachweislich") and its examples ("Claude", "ChatGPT", "Skripte"), which an
        # answer has no reason to repeat. A draft that ignores the posting entirely
        # scores zero - so between a good answer and a bad one the measure reads 0,125
        # against 0. **It does not separate them, so it must not award or withhold
        # points for it**, and no threshold fitted to one draft would change that.
        #
        # This is the rule the verification pass already follows twice and the
        # paragraph criterion once: where the surface is ambiguous the deterministic
        # floor stays silent and the model layer decides. It costs what it costs -
        # with the model layer off, a short form keeps these points. The alternative
        # was a number chosen to make one measurement come out right.
        return Judgement(True)
    needle = content_words(quote)
    if not needle:
        return Judgement(True)
    hit = needle & content_words(ctx.text)
    if len(hit) / len(needle) < settings.min_occasion_overlap:
        return Judgement(
            False,
            f"Greife den Anlass woertlich auf, statt ihn zu umschreiben: \"{quote[:80]}\".",
        )
    return Judgement(True)


def _check_evidence_spread(ctx: DraftContext, settings: Settings) -> Judgement:
    problems: list[str] = []
    for index, paragraph in enumerate(ctx.draft.paragraphs, start=1):
        count = len(paragraph.fact_ids)
        if count > settings.max_facts_per_paragraph:
            problems.append(
                f"Verteile die Belege von {_locate(index, paragraph.text)} auf mehrere "
                f"Absaetze, dort stehen {count} und hoechstens "
                f"{settings.max_facts_per_paragraph} gehoeren in einen"
            )
    for index, text in ctx.body_paragraphs:
        paragraph = ctx.draft.paragraphs[index - 1]
        if not paragraph.fact_ids and len(words(text)) >= settings.min_words_for_citation:
            problems.append(
                f"Belege {_locate(index, text)} mit einer Kennung aus der Faktenbasis "
                "oder streiche ihn"
            )
    if problems:
        return Judgement(False, "; ".join(problems) + ".")
    return Judgement(True)


def _is_delivered(fact: Fact, settings: Settings) -> bool:
    haystack = normalize(f"{fact.claim} {fact.source}")
    return bool(_marker_hits(haystack, settings.delivery_markers))


def _check_delivered(ctx: DraftContext, settings: Settings) -> Judgement:
    available = [fact for fact in ctx.facts if _is_delivered(fact, settings)]
    if not available:
        # Nothing finished in the fact base means nothing to name. A criterion must not
        # ask for evidence the user does not have.
        return Judgement(True)
    cited = set(ctx.draft.fact_ids)
    if any(fact.id in cited for fact in available):
        return Judgement(True)
    example = available[0]
    return Judgement(
        False,
        "Nenne mindestens ein fertiges, belegtes Ergebnis statt nur der Arbeitsweise, "
        f"zum Beispiel '{example.id}': {_excerpt(example.claim, limit=70)}",
    )


#: A sentence opening that points back at what the sentence before it named:
#: definite article plus noun ("Das Setup", "Der Wächter", "Diese Kette"). German makes
#: this the normal way to continue a topic without repeating the word, which is exactly
#: why a shared-word test cannot see the connection.
_BACKREFERENCE_RE = re.compile(
    r"^\s*(?:der|die|das|dieser|diese|dieses|den|dem|deren|dessen)\s+[A-ZÄÖÜ]\w+",
)


#: Adverbs that explicitly mark continuation of the sentence before. Unlike a shared
#: noun these carry the connection grammatically, which is why a shared-word test cannot
#: see it. Deliberately short: only words whose whole job is to tie two statements
#: together, not every conjunction.
_COHESION_MARKERS = frozenset(
    {"dabei", "dazu", "damit", "zudem", "ausserdem", "zusaetzlich", "darueber hinaus",
     "daneben", "ebenso", "gleichzeitig", "davor", "danach", "deshalb", "dadurch"}
)


def _continues_previous(sentence: str) -> bool:
    """True when the sentence is grammatically tied to the one before it.

    Two ways German does that without repeating a noun: opening with a definite article
    plus noun that points back ("Das Setup"), or carrying a cohesion adverb ("dabei",
    "zudem"). Both mean the sentences belong together, and both are invisible to a test
    that looks for shared content words - it sees no overlap and calls good prose a
    topic jump. Where the surface is ambiguous the deterministic floor stays silent;
    that is the same decision the verification pass made when it dropped its
    two-capitals-are-a-name rule, and for the same reason.
    """
    if _BACKREFERENCE_RE.match(sentence):
        return True
    folded = normalize(sentence)
    return any(f" {marker} " in f" {folded} " for marker in _COHESION_MARKERS)


def _refers_back(sentence: str) -> bool:
    """Kept for readability at the call site; see :func:`_continues_previous`."""
    return _continues_previous(sentence)


def _check_one_thought(ctx: DraftContext, settings: Settings) -> Judgement:
    """A paragraph that falls apart into unconnected sentences carries two thoughts.

    Measured over the sentences, not over the cited facts: two facts about the same
    project regularly share no word ("laeuft auf drei Systemen" and "die Testsuite deckt
    die Funktionen ab"), and flagging that would punish a perfectly good paragraph.
    Sentences with fewer than five content words are ignored - a short connecting
    sentence is not a second topic.

    The note quotes the sentence the second thought starts with, because that is the
    one piece of information the reader cannot reconstruct: which paragraph is meant is
    guessable from a number, **where to cut it** is not. And it names the second way
    out, dropping the stray thought, on purpose - splitting a paragraph adds one, which
    can collide with the upper bound stage 6 enforces on paragraph count, and an
    instruction that can only be followed by breaking another rule is not followed at
    all (measured over four rounds of the Talwerk draft, 29.07.2026).
    """
    for index, text in ctx.body_paragraphs:
        # (position of the first sentence, that sentence, the words the group carries)
        groups: list[tuple[int, str, set[str]]] = []
        for position, sentence in enumerate(sentences(text)):
            carried = content_words(sentence)
            if len(carried) < 5:
                continue
            touching = [group for group in groups if group[2] & carried]
            first_position, first_sentence = position, sentence
            merged = set(carried)
            for group in touching:
                if group[0] < first_position:
                    first_position, first_sentence = group[0], group[1]
                merged |= group[2]
                groups.remove(group)
            groups.append((first_position, first_sentence, merged))
        # A group that opens with a back-reference is not proof of a second thought.
        # "Das Setup liegt oeffentlich auf GitHub" continues the sentence before it and
        # shares no content word with it precisely BECAUSE it points back instead of
        # repeating the subject - so the shared-word test reads good German as a topic
        # jump (measured on the Talwerk draft, 29.07.2026, four rounds unresolved). The
        # same reasoning already removed a rule from the verification pass: where German
        # makes the surface ambiguous, the deterministic floor stays silent and the
        # model layer decides. Only groups that name their own subject count here.
        groups = [
            group
            for position, group in enumerate(groups)
            if position == 0 or not _refers_back(group[1])
        ]
        if len(groups) > 1:
            groups.sort(key=lambda group: group[0])
            break_at = _excerpt(groups[1][1])
            return Judgement(
                False,
                f"Teile Absatz {index} vor dem Satz \"{break_at}\": ab dort geht es um "
                "ein anderes Thema als davor. Mach daraus einen eigenen Absatz oder "
                "streiche den zweiten Gedanken - ein Absatz traegt einen Gedanken.",
            )
    return Judgement(True)


def _check_closing(ctx: DraftContext, settings: Settings) -> Judgement:
    if _is_short_form(ctx):
        return Judgement(True, applicable=False)
    tail = [p.text for p in ctx.draft.paragraphs if p.text.strip()]
    if not tail:
        return Judgement(False, "Schliesse mit einer konkreten Frage oder einem konkreten Angebot.")
    # The last two paragraphs, because the question usually stands in front of the
    # sign-off and the sign-off is its own paragraph.
    ending = normalize(" ".join(tail[-2:]))
    if "?" in " ".join(tail[-2:]) or _marker_hits(ending, settings.offers):
        return Judgement(True)
    return Judgement(
        False,
        "Schliesse mit einer konkreten Frage oder einem konkreten Angebot, etwa einem "
        "Terminvorschlag oder einem Link, den du schickst.",
    )


def _check_fillers(ctx: DraftContext, settings: Settings) -> Judgement:
    haystack = normalize(ctx.text)
    hits = _marker_hits(haystack, settings.fillers)
    if hits:
        named = ", ".join(f"'{hit}'" for hit in hits[:3])
        return Judgement(
            False, f"Streiche das Bewerbungsdeutsch und schreibe es einfach: {named}."
        )
    return Judgement(True)


def _check_spelling(ctx: DraftContext, settings: Settings) -> Judgement:
    """Mixed orthography: ASCII transliteration standing next to real umlauts.

    This must NOT go through :func:`words`, which folds umlauts to ASCII for robust
    matching everywhere else. Folding first and then asking "is this a transliteration"
    answers yes for every correctly spelled German word - 'über' folds to 'ueber', which
    is in the list. Measured on 2026-07-29: the criterion reported 'ueber',
    'oeffentlichen' and 'veroeffentlicht' against a draft that spelled all three
    correctly, so it could never be satisfied and put two points permanently out of
    reach. A rubric with an unreachable criterion cannot be answered by revising, only
    by giving up.

    So the raw text is what gets tokenised here, and only a word that really is written
    in ASCII counts.
    """
    text = ctx.text
    has_umlauts = any(character in text for character in "äöüßÄÖÜ")
    raw_words = [match.group(0).lower() for match in re.finditer(r"[^\W\d_]+", text)]
    transliterated = sorted(
        {word for word in raw_words if word in settings.transliterations}
    )
    if has_umlauts and transliterated:
        named = ", ".join(f"'{word}'" for word in transliterated[:3])
        return Judgement(
            False,
            f"Schreibe durchgehend mit echten Umlauten, gemischt ist es ein "
            f"Sorgfaltsfehler: {named}.",
        )
    return Judgement(True)


def _quoted_occasion_words(ctx: DraftContext) -> int:
    """How many words of the draft are the occasion quote another criterion demands.

    ``anlass_konkret`` requires the occasion to be picked up verbatim. Under a small
    sentence cap the two criteria then compete for the same budget: the Nordlicht
    posting allows three sentences, and its occasion is fourteen words - a fifth of the
    allowance, spent before the applicant has written anything. Measured on 2026-07-29
    the generator resolved that conflict the only way it could, by dropping the quote,
    and lost four points to save two.

    Length is meant to measure whether someone padded their own prose. A quotation they
    were obliged to include is not padding, so it does not count against them.
    """
    if ctx.signal is None:
        return 0
    quote = ctx.signal.quote.strip()
    if not quote or normalize(quote) not in normalize(ctx.text):
        return 0
    return len(words(quote))


def _check_length(ctx: DraftContext, settings: Settings) -> Judgement:
    """Whether the draft padded its own argument.

    Counted over the body paragraphs only, and minus the occasion quote another
    criterion obliges the draft to carry. Both exclusions have the same reason: length
    is meant to catch someone spending words on nothing, and neither a sign-off nor a
    mandated quotation is a word anyone chose to spend. Measured on 2026-07-29 the
    difference decided a run - a three-sentence answer naming three built projects with
    a measurement each came to 76 words against a limit of 75, and the eleven words over
    the line were name, mail address, telephone number and profile link.
    """
    body = " ".join(text for _, text in ctx.body_paragraphs)
    count = max(0, len(words(body)) - _quoted_occasion_words(ctx))
    raw_max = ctx.field(_MAX_SENTENCES)
    if raw_max is not None:
        try:
            limit = int(raw_max)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            # The sentence bound itself belongs to form_eingehalten. What is left here
            # is the case of three sentences that run over half a page.
            allowed = limit * settings.words_per_sentence
            if count > allowed:
                return Judgement(
                    False,
                    f"Fasse dich kuerzer: {count} Woerter auf {limit} Saetze sind zu viel, "
                    f"hoechstens {allowed} passen dazu.",
                )
            return Judgement(True)
    if count > settings.max_words:
        return Judgement(
            False,
            f"Kuerze den Entwurf auf hoechstens {settings.max_words} Woerter, er hat {count}.",
        )
    if count < settings.min_words:
        return Judgement(
            False,
            f"Der Entwurf ist mit {count} Woertern zu duenn, mindestens "
            f"{settings.min_words} braucht es, um etwas zu belegen.",
        )
    return Judgement(True)


# ----------------------------------------------------------------------- the defaults

#: Name, weight, purpose, check, fallback note, and whether the model may judge it.
_DEFAULT_CRITERIA: tuple[tuple[str, int, str, Callable[..., Judgement], str, bool], ...] = (
    (
        "form_eingehalten",
        5,
        "Die von der Ausschreibung verlangte Form: Satzanzahl, Beilagen, "
        "ausdrueckliche Verbote wie 'kein Anschreiben'.",
        _check_form,
        "Halte dich an die Form, die die Ausschreibung verlangt.",
        False,  # exakt zaehlbar - kein Modell ueberstimmt eine Zaehlung
    ),
    (
        "anrede_passend",
        4,
        "Du oder Sie wie in der Ausschreibung; der genannte Name wird benutzt.",
        _check_address,
        "Passe die Anrede an die Ausschreibung an.",
        False,  # exakt zaehlbar - kein Modell ueberstimmt eine Zaehlung
    ),
    (
        "anlass_konkret",
        4,
        "Der Anlass aus der Ausschreibung ist aufgegriffen, keine Floskel: in einem "
        "Brief woertlich, in einer verlangten Kurzform inhaltlich beantwortet.",
        _check_occasion,
        "Greife den Anlass aus der Ausschreibung auf - in einem Brief woertlich, in "
        "einer Kurzform, indem du die gestellte Frage beantwortest.",
        True,
    ),
    (
        "belege_verteilt",
        3,
        "Kein Absatz traegt mehr als drei Belege, kein Sachabsatz steht ohne Beleg.",
        _check_evidence_spread,
        "Verteile die Belege gleichmaessig auf die Absaetze.",
        False,
    ),
    (
        "ausgeliefertes_genannt",
        4,
        "Mindestens ein fertiges, belegtes Ergebnis kommt vor, nicht nur die Arbeitsweise.",
        _check_delivered,
        "Nenne ein fertiges, belegtes Ergebnis, nicht nur die Arbeitsweise.",
        True,
    ),
    (
        "ein_gedanke_je_absatz",
        3,
        "Kein Absatz springt zwischen zusammenhanglosen Themen.",
        _check_one_thought,
        "Teile Absaetze, die zwei Themen mischen, in je einen Gedanken.",
        True,
    ),
    (
        "abschluss_konkret",
        3,
        "Der Text endet mit einer konkreten Frage oder einem konkreten Angebot.",
        _check_closing,
        "Schliesse mit einer konkreten Frage oder einem konkreten Angebot.",
        False,  # exakt zaehlbar - kein Modell ueberstimmt eine Zaehlung
    ),
    (
        "keine_floskeln",
        3,
        "Keine Superlative ueber sich selbst, kein Bewerbungsdeutsch, kein Konjunktiv.",
        _check_fillers,
        "Streiche das Bewerbungsdeutsch und schreibe es einfach.",
        False,  # exakt zaehlbar - kein Modell ueberstimmt eine Zaehlung
    ),
    (
        "orthographie_einheitlich",
        2,
        "Keine gemischte Schreibung: ASCII-Umschrift neben echten Umlauten.",
        _check_spelling,
        "Schreibe durchgehend mit echten Umlauten.",
        False,
    ),
    (
        "laenge_angemessen",
        2,
        "Die Laenge bleibt im gesetzten oder im vernuenftigen Rahmen.",
        _check_length,
        "Bring den Entwurf auf eine angemessene Laenge.",
        False,
    ),
)

_DEFAULT_WEIGHTS = {name: weight for name, weight, *_ in _DEFAULT_CRITERIA}


@dataclass(frozen=True, slots=True)
class Rubric:
    """The criteria, their weights and the settings they read.

    Args:
        criteria: The active criteria, in report order.
        settings: Numbers and lexicons.
        max_revisions: How often a draft may be revised before the best attempt goes to
            the person. Lives here and not in the configuration file because it is the
            same decision as the rubric itself - how good a draft has to be before it is
            shown to somebody.
        use_model: Whether the critic asks a model on top of the deterministic checks.
            Off by default: the deterministic layer runs offline in milliseconds, the
            model layer costs a call per round and per draft.
    """

    criteria: tuple[Criterion, ...]
    settings: Settings = Settings()
    max_revisions: int = 3
    use_model: bool = False

    @property
    def possible(self) -> int:
        """Every point that can be earned."""
        return sum(criterion.weight for criterion in self.criteria)

    def criterion(self, name: str) -> Criterion | None:
        for entry in self.criteria:
            if entry.name == name:
                return entry
        return None

    def judge(self, ctx: DraftContext) -> list[CriterionResult]:
        """Run every criterion. Deterministic, no model, no network."""
        results: list[CriterionResult] = []
        for criterion in self.criteria:
            judgement = criterion.check(ctx, self.settings)
            applicable = getattr(judgement, "applicable", True)
            results.append(
                CriterionResult(
                    name=criterion.name,
                    weight=criterion.weight,
                    earned=criterion.weight if (judgement.passed and applicable) else 0,
                    possible=criterion.weight if applicable else 0,
                    note=(
                        ""
                        if judgement.passed or not applicable
                        else (judgement.note or criterion.fallback_note)
                    ),
                )
            )
        return results


def default_rubric() -> Rubric:
    """The criteria of the task, with their default weights."""
    return Rubric(
        criteria=tuple(
            Criterion(
                name=name,
                weight=weight,
                purpose=purpose,
                check=check,
                fallback_note=fallback,
                model_checkable=model_checkable,
            )
            for name, weight, purpose, check, fallback, model_checkable in _DEFAULT_CRITERIA
        )
    )


# ------------------------------------------------------------------------ the file


def _string_set(raw: Any, fallback: frozenset[str], *, where: str) -> frozenset[str]:
    if raw is None:
        return fallback
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        raise ConfigError(f"'{where}' muss eine Liste von Zeichenketten sein.")
    return frozenset(fold(entry).strip() for entry in raw if entry.strip())


def _settings_from(raw: Any) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, Mapping):
        raise ConfigError("Der Abschnitt 'settings' in der Rubrik muss eine Zuordnung sein.")
    numbers: dict[str, Any] = {}
    for key in (
        "max_facts_per_paragraph",
        "min_words_for_citation",
        "min_words",
        "max_words",
        "words_per_sentence",
    ):
        if key in raw:
            try:
                numbers[key] = int(raw[key])
            except (TypeError, ValueError):
                raise ConfigError(f"'settings.{key}' in der Rubrik muss eine Zahl sein.") from None
    if "min_occasion_overlap" in raw:
        try:
            numbers["min_occasion_overlap"] = float(raw["min_occasion_overlap"])
        except (TypeError, ValueError):
            raise ConfigError("'settings.min_occasion_overlap' muss eine Zahl sein.") from None
    return Settings(
        fillers=_string_set(raw.get("fillers"), DEFAULT_FILLERS, where="settings.fillers"),
        transliterations=_string_set(
            raw.get("transliterations"), DEFAULT_TRANSLITERATIONS, where="settings.transliterations"
        ),
        delivery_markers=_string_set(
            raw.get("delivery_markers"), DEFAULT_DELIVERY_MARKERS, where="settings.delivery_markers"
        ),
        offers=_string_set(raw.get("offers"), DEFAULT_OFFERS, where="settings.offers"),
        **numbers,
    )


def load_rubric(path: str | Path) -> Rubric:
    """Read a rubric file. Unknown criteria are an error, missing ones keep their weight.

    An unknown name is refused rather than ignored: a typo in a criterion name would
    otherwise silently switch off a check, and a quality measure that quietly stops
    measuring is worse than none.

    Raises:
        anlass.errors.ConfigError: File missing or malformed, unknown criterion name,
            negative weight, every criterion switched off.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Die Rubrik '{path}' gibt es nicht.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Die Datei '{path}' ist kein gueltiges YAML: {exc}") from exc
    if data is None:
        return default_rubric()
    if not isinstance(data, Mapping):
        raise ConfigError(f"Die Rubrik '{path}' muss ein Objekt enthalten.")

    weights = dict(_DEFAULT_WEIGHTS)
    raw_criteria = data.get("criteria")
    if raw_criteria is not None:
        if not isinstance(raw_criteria, Mapping):
            raise ConfigError(
                f"In '{path}' muss 'criteria' eine Zuordnung von Name auf Gewicht sein."
            )
        for name, value in raw_criteria.items():
            if name not in weights:
                raise ConfigError(
                    f"'{name}' in '{path}' ist kein bekanntes Kriterium. Moeglich sind: "
                    + ", ".join(sorted(weights))
                )
            try:
                weight = int(value)
            except (TypeError, ValueError):
                raise ConfigError(f"Das Gewicht von '{name}' in '{path}' muss eine Zahl sein.") from None
            if weight < 0:
                raise ConfigError(f"Das Gewicht von '{name}' in '{path}' darf nicht negativ sein.")
            weights[name] = weight

    base = default_rubric()
    criteria = tuple(
        Criterion(
            name=entry.name,
            weight=weights[entry.name],
            purpose=entry.purpose,
            check=entry.check,
            fallback_note=entry.fallback_note,
            model_checkable=entry.model_checkable,
        )
        for entry in base.criteria
        if weights[entry.name] > 0
    )
    if not criteria:
        raise ConfigError(
            f"In '{path}' ist jedes Kriterium auf 0 gesetzt. Dann misst niemand mehr die "
            "Guete, und das ist genau der Zustand, den diese Datei beheben soll."
        )
    try:
        max_revisions = int(data.get("max_revisions", base.max_revisions))
    except (TypeError, ValueError):
        raise ConfigError(f"'max_revisions' in '{path}' muss eine Zahl sein.") from None
    if max_revisions < 0:
        raise ConfigError(f"'max_revisions' in '{path}' darf nicht negativ sein.")
    return Rubric(
        criteria=criteria,
        settings=_settings_from(data.get("settings")),
        max_revisions=max_revisions,
        use_model=bool(data.get("use_model", base.use_model)),
    )


def rubric_for_profile(profile_dir: str | Path | None) -> Rubric:
    """The user's rubric if there is one, the shipped default otherwise.

    A missing file is not an error - the defaults are the ones the task names, and a
    tool that demands a configuration file before it measures anything would make the
    measure optional.
    """
    if profile_dir is None:
        return default_rubric()
    path = Path(profile_dir) / RUBRIC_FILE
    return load_rubric(path) if path.is_file() else default_rubric()
