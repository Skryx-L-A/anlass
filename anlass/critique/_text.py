"""Text helpers the rubric shares. German prose, no model, no network.

Two decisions here shape every criterion built on top.

**Words are folded before they are compared.** ``laeuft`` and ``läuft`` are the same
word written twice, and a fact base written in ASCII transliteration next to a model
that sets real umlauts is exactly the case that produced finding Q7. A comparison that
treats the two as different words would silently find no overlap where a reader sees
the same sentence.

**Only content words count.** ``und``, ``mit``, ``dass`` connect everything with
everything; an overlap measured over them measures grammar, not subject matter. The
stop word list is short on purpose - it holds the words that carry no topic, not every
frequent word.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = [
    "CLOSING_MARKERS",
    "SALUTATION_MARKERS",
    "body_sentence_count",
    "content_words",
    "fold",
    "is_closing",
    "is_salutation",
    "normalize",
    "sentences",
    "words",
]

_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß'’-]*")

#: Abbreviations whose full stop does not end a sentence. Masked before splitting and
#: put back afterwards - without them "z. B." counts as two sentences and every check
#: on the sentence count of a three-sentence application is wrong by a third.
_ABBREVIATIONS = (
    "z. B.", "z.B.", "u. a.", "u.a.", "d. h.", "d.h.", "bzw.", "ca.", "ggf.",
    "usw.", "etc.", "Nr.", "Dr.", "Prof.", "Mio.", "Mrd.", "inkl.", "evtl.",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"'“”»)\]]*\s+")

#: German words that carry no topic. Deliberately short: this list decides what an
#: overlap measures, and every word added to it makes two paragraphs look more alike.
_STOPWORDS = frozenset(
    {
        "aber", "alle", "allem", "allen", "aller", "alles", "also", "andere", "anderen",
        "auch", "auf", "aus", "bei", "beim", "bin", "bis", "damit", "dann", "dass",
        "dein", "deine", "dem", "den", "denen", "der", "des", "die", "dies", "diese",
        "diesem", "diesen", "dieser", "dieses", "doch", "dort", "durch", "ein", "eine",
        "einem", "einen", "einer", "eines", "etwa", "euch", "euer", "fuer", "ganz",
        "gegen", "gerne", "habe", "haben", "hier", "ihr", "ihre", "ihrem", "ihren",
        "ihrer", "ihres", "immer", "ist", "kann", "koennen", "mehr", "mein", "meine",
        "meinem", "meinen", "meiner", "mich", "mir", "mit", "nach", "nicht", "noch",
        "nur", "oder", "ohne", "schon", "sehr", "sein", "seine", "seinem", "seinen",
        "sich", "sind", "soll", "sollen", "sowie", "ueber", "und", "unser", "unsere",
        "vier", "vom", "von", "vor", "waehrend", "war", "waren", "was", "wenn", "werde",
        "werden", "wie", "wieder", "will", "wir", "wird", "wurde", "wurden", "zum",
        "zur", "zwei", "zwischen",
    }
)

#: Openings that mark a paragraph as a salutation. A salutation carries no statement,
#: so it is exempt from the criteria that ask for evidence and for one thought.
SALUTATION_MARKERS = (
    "sehr geehrte", "sehr geehrter", "guten tag", "hallo", "liebe ", "lieber ",
    "moin", "servus",
)

#: Closings, same reason. Written folded, because that is the form they are compared
#: against - an entry with real umlauts would never match anything.
CLOSING_MARKERS = (
    "viele gruesse", "beste gruesse", "freundliche gruesse", "herzliche gruesse",
    "mit freundlichen gruessen", "gruss",
)


def fold(word: str) -> str:
    """Lower case with umlauts written out, so both spellings compare equal."""
    lowered = word.lower()
    for umlaut, ascii_form in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(umlaut, ascii_form)
    return lowered


def normalize(text: str) -> str:
    """Folded, lower case, single blanks. What every substring test runs against."""
    return " ".join(fold(text).split())


def words(text: str) -> list[str]:
    """Every word of ``text``, folded, in order."""
    return [fold(match.group(0)) for match in _WORD_RE.finditer(text)]


def content_words(text: str, *, minimum_length: int = 4) -> set[str]:
    """The topic-carrying words: long enough, not a stop word, folded."""
    return {
        word
        for word in words(text)
        if len(word) >= minimum_length and word not in _STOPWORDS
    }


def sentences(text: str) -> list[str]:
    """Split into sentences. Abbreviations are masked, so ``z. B.`` stays one word."""
    masked = text
    for index, abbreviation in enumerate(_ABBREVIATIONS):
        masked = masked.replace(abbreviation, f"\x00{index}\x00")
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(masked)]
    restored: list[str] = []
    for part in parts:
        for index, abbreviation in enumerate(_ABBREVIATIONS):
            part = part.replace(f"\x00{index}\x00", abbreviation)
        if part:
            restored.append(part)
    return restored


def is_salutation(paragraph: str) -> bool:
    """Whether a paragraph opens the letter rather than saying something."""
    return normalize(paragraph).startswith(SALUTATION_MARKERS)


def is_closing(paragraph: str) -> bool:
    """Whether a paragraph is the sign-off."""
    return any(marker in normalize(paragraph) for marker in CLOSING_MARKERS)


def body_sentence_count(paragraphs: Iterable[str]) -> int:
    """Sentences in the paragraphs that say something. The one sentence count.

    "Beschreibe in 3 Saetzen, welche KI-Projekte du gebaut hast" asks for three
    sentences of description. "Hallo Mira," is not one of them, and neither is "Viele
    Gruesse" - so a salutation and a sign-off are left out, exactly as
    :attr:`anlass.critique.rubric.DraftContext.body_paragraphs` leaves them out
    everywhere else.

    It lives here because **two stages have to answer this question with the same
    number.** Stage 6 refuses its own answer over the cap, the rubric takes points away
    for it; when the two counted differently, a draft could be accepted by the generator
    and lose the points anyway, which puts full marks out of reach for a draft that did
    nothing wrong. That class of defect has now been found three times in this rubric
    (Q12, Q15, Q17), always where one measure disagreed with another. One function, two
    callers, no way to drift apart.
    """
    return sum(
        len(sentences(text))
        for text in paragraphs
        if text.strip() and not is_salutation(text) and not is_closing(text)
    )
