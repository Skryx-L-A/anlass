"""Stage 6b: measure how good a draft is, not whether it is true.

Stage 7 answers one question - does the text claim something no fact carries. That
leaves every quality defect unmeasured: the demanded form ignored, the wrong salutation,
the weakest occasion, a paragraph that reads as a catalogue, no delivered product named.
None of it is a fabrication, so none of it was ever caught (Q8).

:mod:`anlass.critique.rubric` holds the criteria, :mod:`anlass.critique.critic` runs
them and turns what is open into revision notes. The loop that uses both lives in
:mod:`anlass.pipeline`.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..models import Draft, Fact, Lead, Signal, VerificationResult
from .critic import Critique, RubricCritic
from .rubric import (
    Criterion,
    CriterionResult,
    DraftContext,
    Judgement,
    RUBRIC_FILE,
    Rubric,
    Settings,
    default_rubric,
    load_rubric,
    rubric_for_profile,
)

__all__ = [
    "Critic",
    "Criterion",
    "CriterionResult",
    "Critique",
    "DraftContext",
    "Judgement",
    "RUBRIC_FILE",
    "Rubric",
    "RubricCritic",
    "Settings",
    "default_rubric",
    "load_rubric",
    "rubric_for_profile",
]


@runtime_checkable
class Critic(Protocol):
    """What the pipeline needs from a quality measure.

    This protocol belongs next to the others in :mod:`anlass.interfaces`; it lives here
    while that file is held by another part of this phase. The pipeline imports it from
    here, so moving it later is one import line, not a rewrite.
    """

    @property
    def name(self) -> str:
        """Stable identifier. Written into the run protocol."""

    def critique(
        self,
        draft: Draft,
        *,
        lead: Lead | None = None,
        signal: Signal | None = None,
        facts: Sequence[Fact] = (),
        verification: VerificationResult | None = None,
    ) -> Critique:
        """Judge one draft and name, per open criterion, what to do about it.

        Never raises because of a bad draft - a bad draft is a low score with notes,
        not an exception.
        """
