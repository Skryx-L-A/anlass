"""Stage 5: find the quotable occasion for contacting this recipient now.

Ships one detector, :class:`JobRequirementSignalDetector`: it holds each sentence of
the lead's source text against the user's fact base and returns a signal only where
both agree - a requirement the posting states, verbatim, and a fact the user can back
it up with. A quote that cannot be located in the source text is not returned;
:class:`anlass.models.Signal` enforces that itself (``__post_init__`` also rejects an
empty quote).

Ranking is a length-normalised, rarity-weighted overlap, not a raw keyword count (see
:func:`_similarity` and :func:`_idf_weights`) - a raw count let a long, generically
worded fact outrank a short, sharply relevant one just by having more keywords to
overlap with. On top of similarity two things carry weight, because an occasion is not
the most similar sentence but the strongest one: a sentence that states a requirement
(:data:`_REQUIREMENT_BOOST`) and a fact that names a strength rather than mere
eligibility (:data:`_STRENGTH_BOOST`). Weak matches are dropped outright
(``min_similarity``): below that bar a shared word is coincidence, not an occasion.
Results are capped at ``max_signals`` and deduplicated per backing fact, so a handful
of leftover facts do not crowd out the rest of the fact base in whatever gets drafted
from them.

Other occasion types (a funding round, a technology change) are meant to live next to
this one as their own class implementing :class:`anlass.interfaces.SignalDetector`
over a different slice of the lead - :class:`CompositeSignalDetector` is the seam they
plug into. Nothing here assumes what kind of occasion a detector looks for or how many
there are; adding one does not touch this file.
"""

from __future__ import annotations

import math
import re
from typing import Mapping, Sequence

from ..critique.rubric import is_strength
from ..models import Fact, Field, Lead, Signal

__all__ = ["CompositeSignalDetector", "JobRequirementSignalDetector"]

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Deliberately wider than a short "and/or/the" list: German inflects articles and
# prepositions by case (der/die/das/des/dem/den ...), and an incomplete list lets a
# grammatical particle slip through as a "keyword" that then matches almost any
# sentence - exactly the kind of accidental overlap the rarity weighting below is
# meant to catch, but cheaper to just exclude outright.
_STOPWORDS = frozenset(
    """
    und oder aber sondern denn weil da dass ob wie wo so als auch
    der die das des dem den ein eine einer einem einen eines
    mit fuer von zu zum zur zwischen bis am beim vom im in ins auf an bei
    nach vor ueber unter durch gegen ohne seit trotz waehrend wegen um neben hinter
    ist sind war waren wird werden wurde wurden hat hatte hatten haben hast habe
    kann koennen konnte muss musst muessen musste soll sollst sollen will willst wollen
    wir sie ihr ihre du dich dir mich mir er es sein seine unser euer
    nicht kein keine keinen keiner noch sehr gut mehr schon
    uns euch ihnen ihm ihn man alle alles etwas jede jeder jedes diese dieser dieses
    the and for with from into your you our we are is was were will can have has
    this that these those
    """.split()
)

_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9+.#-]{2,}")

# A sentence that states a requirement ("Pflicht", "Voraussetzung ...") is worth more
# as an occasion than one that merely describes the company or a nice-to-have - it is
# where most applicants fail and therefore the sharpest possible opening. Detection
# stays a fixed word list on purpose: good enough to prefer requirement sentences among
# otherwise-comparable matches, not a claim to understand the sentence.
_REQUIREMENT_RE = re.compile(
    r"voraussetzung|pflicht|erforderlich|verpflichtend|\bdu hast\b|\bmuss(t)?\b"
    r"|\brequired\b|\bmust\b",
    re.IGNORECASE,
)
_REQUIREMENT_BOOST = 1.3

# A fact that names something built, delivered or measured (:func:`anlass.critique.
# rubric.is_strength`) answers "why this applicant"; a language or a degree answers only
# "may this applicant". The occasion built on the first is worth more even where the
# second matches a sentence more literally - which is exactly what happened on
# 2026-07-29: for Nordlicht the language sentence beat "Du hast nachweislich schon etwas
# mit KI gebaut oder automatisiert", and the letter that followed opened with the one
# sentence about the applicant that distinguishes nobody.
#
# Measured on that data, with the requirement boost already applied to both: the sharp
# sentence scored 0.1294 against 0.1662 for the language sentence, so 1.3 flips it by
# 1.2 % (a single word of rewording flips it back) and 1.5 flips it by 17 %. 1.5 says:
# a proven strength wins unless the eligibility match is half again as similar. Above
# that the bonus would start beating clearly better matches, which is a different bug.
_STRENGTH_BOOST = 1.5


def _keywords(text: str) -> set[str]:
    keywords: set[str] = set()
    for raw in _WORD_RE.findall(text):
        # The word regex allows "." inside a token (so "Node.js", "n8n" survive), but
        # that also glues the sentence-ending period onto the last word ("Ausprobieren.").
        # That stray period made the last word of every sentence unmatchable against
        # the same word anywhere else - stripping a trailing "." is the fix.
        word = raw.rstrip(".").lower()
        if len(word) >= 3 and word not in _STOPWORDS:
            keywords.add(word)
    return keywords


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _looks_like_requirement(sentence: str) -> bool:
    return _REQUIREMENT_RE.search(sentence) is not None


def _idf_weights(keyword_sets: Sequence[set[str]]) -> dict[str, float]:
    """Inverse document frequency over ``keyword_sets`` - one weight per word.

    A word occurring in almost every set ("Aufgaben", "Modell", "Team") ends up with a
    weight near the floor of 1.0; a word unique to a single set ("Homebrew", "I2S",
    "Latenz") gets the highest weight. Smoothed (``+1`` on both sides of the ratio) so
    a word in *every* set still gets a small positive weight instead of zero, and an
    empty corpus cannot divide by zero. Computed fresh per :meth:`detect` call from the
    posting's own sentences plus the fact base - no external corpus, no network, no
    model, so the deterministic path this stage promises stays deterministic.
    """
    total_documents = len(keyword_sets)
    document_frequency: dict[str, int] = {}
    for keywords in keyword_sets:
        for word in keywords:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    return {
        word: math.log((1 + total_documents) / (1 + count)) + 1.0
        for word, count in document_frequency.items()
    }


def _similarity(a: set[str], b: set[str], weights: Mapping[str, float]) -> float:
    """Weighted Jaccard: rarity-weighted overlap over rarity-weighted union.

    Dividing by the union rather than counting raw overlap is what removes the length
    bias: a long, generically worded fact claim also has a large keyword set, so it
    needs a correspondingly large union with any given sentence, which drags its score
    back down even where it happens to share a few common words with everything.
    Weighting each word by rarity (see :func:`_idf_weights`) is what makes the
    surviving overlap mean something: two sentences sharing "Team" and "Aufgaben" do
    not outrank one sharing "Homebrew".
    """
    shared = a & b
    if not shared:
        return 0.0
    union = a | b
    return sum(weights[word] for word in shared) / sum(weights[word] for word in union)


class JobRequirementSignalDetector:
    """Matches requirement sentences of a job posting against the fact base.

    A sentence becomes a candidate when it shares at least one significant word with a
    fact's claim - a small, honest heuristic (keyword overlap, not semantic matching),
    documented as such rather than dressed up as more than it is. Candidates are then
    ranked by length-normalised, rarity-weighted overlap (:func:`_similarity`), a
    requirement sentence ("Pflicht", "Voraussetzung ...") and a fact that names a proven
    strength each get a fixed boost in that ranking, and anything below
    ``min_similarity`` is dropped as coincidence rather than occasion - project rule 1
    ("kein Kontakt ohne Anlass") reads that literally: no signal is an honest result, a
    random shared word is not a signal.

    The returned list holds at most ``max_signals`` entries, one per distinct backing
    fact (the strongest sentence wins when several match the same fact), so a caller
    that uses more than the first signal draws its evidence from more than one corner
    of the fact base instead of all from the single fact that happened to win.
    """

    def __init__(
        self,
        facts: Sequence[Fact],
        *,
        name: str = "job_requirement",
        max_signals: int = 3,
        min_similarity: float = 0.06,
    ) -> None:
        self._facts = list(facts)
        self._name = name
        self._max_signals = max_signals
        self._min_similarity = min_similarity

    @property
    def name(self) -> str:
        return self._name

    def detect(self, lead: Lead) -> list[Signal]:
        if not lead.text.strip() or not self._facts:
            return []
        sentences = _sentences(lead.text)
        if not sentences:
            return []

        fact_keywords = [(fact.id, _keywords(fact.claim)) for fact in self._facts]
        strong = {fact.id: is_strength(fact) for fact in self._facts}
        sentence_keywords = [(sentence, _keywords(sentence)) for sentence in sentences]
        weights = _idf_weights(
            [keywords for _, keywords in sentence_keywords] + [keywords for _, keywords in fact_keywords]
        )

        # (ranking score, sentence, fact_id) - one row per sentence, its single
        # strongest-matching fact only, and only once it clears the minimum.
        candidates: list[tuple[float, str, str]] = []
        for sentence, sentence_kw in sentence_keywords:
            if not sentence_kw:
                continue
            best_score = 0.0
            best_fact_id = ""
            for fact_id, fact_kw in fact_keywords:
                score = _similarity(sentence_kw, fact_kw, weights)
                if score > best_score:
                    best_score = score
                    best_fact_id = fact_id
            if best_score < self._min_similarity:
                continue
            # Which fact backs a sentence is decided by similarity alone, and both
            # bonuses are applied afterwards. Letting the strength bonus pick the fact
            # was tried and measured on 2026-07-29: "Verhandlungssicheres Deutsch,
            # sicheres Englisch" was then backed by the latency measurement, because
            # that fact names Deutsch and Englisch too and the bonus carried it past the
            # language fact. That is a worse occasion, not a stronger one - the bonus is
            # meant to rank occasions, not to re-attribute sentences.
            ranking_score = best_score
            if _looks_like_requirement(sentence):
                ranking_score *= _REQUIREMENT_BOOST
            if strong[best_fact_id]:
                ranking_score *= _STRENGTH_BOOST
            candidates.append((ranking_score, sentence, best_fact_id))

        # Keep only the strongest sentence per fact, so the top results span
        # different facts instead of all being pulled from whichever fact happened
        # to overlap with the most sentences.
        best_per_fact: dict[str, tuple[float, str, str]] = {}
        for ranking_score, sentence, fact_id in candidates:
            current = best_per_fact.get(fact_id)
            if current is None or ranking_score > current[0]:
                best_per_fact[fact_id] = (ranking_score, sentence, fact_id)

        ranked = sorted(best_per_fact.values(), key=lambda c: c[0], reverse=True)[: self._max_signals]

        source_url = lead.value(Field.URL)
        signals: list[Signal] = []
        for _, sentence, fact_id in ranked:
            start = lead.text.find(sentence)
            if start < 0:
                continue
            signals.append(
                Signal(
                    lead_id=lead.id,
                    kind=f"requirement:{fact_id}",
                    quote=sentence,
                    source_url=source_url,
                    start=start,
                    end=start + len(sentence),
                    detector=self.name,
                )
            )
        return signals


class CompositeSignalDetector:
    """Runs several detectors and concatenates their signals.

    The seam a future occasion type (funding round, technology change) plugs into:
    implement :class:`anlass.interfaces.SignalDetector` and add an instance here. Each
    detector's own results stay in the order that detector returns them (strongest
    first, per the protocol); across detectors, results follow the order the detectors
    were given - there is no single "strength" that compares a requirement match
    against, say, a funding-round match, so this does not invent one.
    """

    def __init__(self, detectors: Sequence[object], *, name: str = "composite") -> None:
        self._detectors = list(detectors)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def detect(self, lead: Lead) -> list[Signal]:
        signals: list[Signal] = []
        for detector in self._detectors:
            signals.extend(detector.detect(lead))  # type: ignore[attr-defined]
        return signals
