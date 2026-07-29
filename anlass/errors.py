"""Exception types. Messages are German because a user reads them."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for the annotation below
    from .models import Draft


class AnlassError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(AnlassError):
    """Configuration or profile data is missing, malformed or contradictory."""


class LLMError(AnlassError):
    """A language model provider could not be reached or answered unusably."""


class SourceError(AnlassError):
    """A source (stage 1) or a network-backed enrichment provider (stage 3) could
    not deliver: unreachable URL, malformed feed, unrecognisable file shape."""


class EnrichError(AnlassError):
    """An enrichment provider (stage 3) could not fill its fields: a model answered
    with something that is not the expected structure."""


class DraftError(AnlassError):
    """A draft could not be produced within the grounding rules.

    Args:
        draft: The rejected text, where there is one. Stage 6 refuses its own answer
            when it breaks a countable rule of the posting - too many sentences, the
            wrong form of address - and that answer still exists. Carrying it out with
            the refusal is what lets the caller keep it: four model calls that all
            broke the sentence cap are four texts, and the best of them belongs in
            front of a person with the violation named, not in the bin. ``None`` where
            there really is nothing - unparsable JSON, a model that answered with no
            paragraphs at all.
        violation: Short machine-comparable name of the rule that was broken
            ("satzanzahl", "anrede", "eintragsanzahl"), empty where none applies. The
            message cannot serve for that: two rounds that both broke the sentence cap
            say "der Entwurf hat 3" and "der Entwurf hat 4", which is the same defect
            written twice. The caller compares this to see that a round failed the same
            way as the one before it, and that asking a third time is pointless
            (Q19: five of six rounds on one violation).
    """

    def __init__(
        self, message: str, *, draft: "Draft | None" = None, violation: str = ""
    ) -> None:
        super().__init__(message)
        self.draft = draft
        self.violation = violation


class StoreError(AnlassError):
    """The store could not read or write the requested record."""


class SetupError(AnlassError):
    """The setup could not be finished: the input ended, it was interrupted, or a
    mandatory entry has no answer and no default. Raised instead of letting an
    ``EOFError`` reach the user as a stack trace."""
