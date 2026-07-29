"""Core data types of the anlass pipeline.

Everything that travels between two pipeline stages is defined here. Two properties
are load-bearing and must not be optimised away:

* ``FieldValue`` carries provenance. Without it nobody can later tell whether a value
  came out of the source text or out of a language model's guess - and grounding
  fails exactly there (plan, stage 3).
* ``Draft`` carries the fact ids used *per paragraph*. The verification pass needs
  that mapping; a flat list of sources would let a paragraph borrow evidence it never
  cited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ApprovalRecord",
    "ApprovalState",
    "CriterionOutcome",
    "Delivery",
    "Fact",
    "Field",
    "FieldValue",
    "Finding",
    "FindingKind",
    "FollowUp",
    "Draft",
    "Lead",
    "OutboundMessage",
    "Paragraph",
    "RawRecord",
    "Reply",
    "ReplyKind",
    "ScoreResult",
    "Severity",
    "Signal",
    "TransportReceipt",
    "VerificationResult",
    "new_id",
    "utcnow",
]


def utcnow() -> datetime:
    """Timezone-aware current time. Every timestamp in this package is UTC."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Short, collision-safe identifier with a readable prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Field:
    """Well-known field names of a ``Lead``.

    The field map stays open - a source or enrich provider may add its own names.
    These constants only pin down the ones the shipped stages agree on, so that a
    scorer written in phase 2 does not have to guess whether the key is ``company``
    or ``organization``.
    """

    ORGANIZATION = "organization"
    ROLE = "role"
    URL = "url"
    LOCATION = "location"
    REMOTE = "remote"
    EMPLOYMENT_TYPE = "employment_type"
    WORKLOAD_HOURS = "workload_hours"
    HEADCOUNT = "headcount"
    TECH_STACK = "tech_stack"
    REQUIRED_SKILLS = "required_skills"
    CONTACT_NAME = "contact_name"
    CONTACT_EMAIL = "contact_email"
    PUBLISHED_AT = "published_at"

    #: What the posting asks the application itself to look like, e.g. "drei Saetze
    #: zu zuletzt gebauten Projekten" or "GitHub-Link plus zwei Saetze" (phase 5b,
    #: Q1: the draft stage never knew this was asked for).
    APPLICATION_FORMAT = "application_format"
    #: Where the application goes: a form, an address, a message to a named person.
    #: Distinct from :data:`anlass.enrich.providers.extract.CONTACT_CHANNEL_FIELD`
    #: (the generic "how to reach them"): this one is read from the posting's own
    #: application instructions and may name a different route.
    APPLICATION_CHANNEL = "application_channel"
    #: List of what is explicitly asked for: repo link, video, CV, portfolio.
    REQUIRED_ARTIFACTS = "required_artifacts"
    #: List of what is explicitly *not* wanted, e.g. "Anschreiben". The reason this
    #: whole set of fields exists (Q1) - never leave it unfilled
    #: just because nothing is mentioned; an empty list is the honest answer then.
    FORBIDDEN_ARTIFACTS = "forbidden_artifacts"
    #: Upper bound on sentence count if the posting names one, else unset.
    MAX_SENTENCES = "max_sentences"
    #: "du" or "sie", derived from how the posting addresses *the reader* ("Deine
    #: Aufgaben" vs. "Ihre Aufgaben") - not a style choice, a fact read off the text.
    FORM_OF_ADDRESS = "form_of_address"


@dataclass(frozen=True, slots=True)
class FieldValue:
    """A single field of a lead together with its provenance.

    Args:
        value: The value itself. Any JSON-serialisable Python object.
        provider: Name of the provider that filled the field ("source", the name of an
            ``EnrichProvider``, or the model name if a model extracted it).
        retrieved_at: When the provider produced the value.
        confidence: 0.0 to 1.0. A provider that reads a value verbatim out of the
            source text reports 1.0; an extraction by a model reports less.
        evidence: Optional verbatim excerpt the value was taken from. Not required by
            the plan, but the cheapest way to make an enrichment auditable later.
    """

    value: Any
    provider: str
    retrieved_at: datetime = field(default_factory=utcnow)
    confidence: float = 1.0
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("FieldValue braucht einen Anbieternamen (Herkunft ist Pflicht).")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Zuversicht muss zwischen 0.0 und 1.0 liegen.")


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One untouched record as a ``Source`` delivered it (stage 1)."""

    source: str
    external_id: str
    text: str
    url: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=utcnow)


@dataclass
class Lead:
    """A normalised contact candidate (stage 2).

    ``fields`` is the only place values live, and every entry carries its provenance.
    Read values through :meth:`value`; write them through :meth:`set` so the waterfall
    in stage 3 keeps the first provider that filled a field.
    """

    source: str
    source_ref: str | None = None
    text: str = ""
    fields: dict[str, FieldValue] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("lead"))
    created_at: datetime = field(default_factory=utcnow)

    def value(self, name: str, default: Any = None) -> Any:
        """Plain value of a field, or ``default`` if it is not filled."""
        entry = self.fields.get(name)
        return default if entry is None else entry.value

    def provider_of(self, name: str) -> str | None:
        """Which provider filled ``name``, or ``None`` if it is not filled."""
        entry = self.fields.get(name)
        return None if entry is None else entry.provider

    def set(self, name: str, value: FieldValue, *, overwrite: bool = False) -> bool:
        """Set a field. Returns ``True`` if it was written.

        The waterfall relies on the default: an already filled field is kept, so the
        first (usually most trustworthy) provider wins.
        """
        if name in self.fields and not overwrite:
            return False
        self.fields[name] = value
        return True

    def missing(self, names: Iterable[str]) -> list[str]:
        """Those of ``names`` that are not filled yet."""
        return [n for n in names if n not in self.fields]


@dataclass(frozen=True, slots=True)
class Signal:
    """The quotable occasion for contacting exactly this recipient now (stage 5).

    ``quote`` must be verbatim source text, ``start``/``end`` its offsets in the text
    the signal was found in, so a reviewer can look it up instead of trusting it.
    """

    lead_id: str
    kind: str
    quote: str
    source_url: str | None = None
    start: int | None = None
    end: int | None = None
    detector: str = ""
    id: str = field(default_factory=lambda: new_id("sig"))
    detected_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.quote.strip():
            raise ValueError("Ein Anlass ohne Zitat ist kein Anlass.")


@dataclass(frozen=True, slots=True)
class Fact:
    """One entry of the user's fact base: claim plus where it is documented."""

    id: str
    claim: str
    source: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Ein Fakt braucht eine Kennung.")
        if not self.claim.strip():
            raise ValueError(f"Fakt '{self.id}' hat keine Aussage.")
        if not self.source.strip():
            raise ValueError(f"Fakt '{self.id}' hat keine Fundstelle.")


@dataclass(frozen=True, slots=True)
class Paragraph:
    """One paragraph of a draft plus the fact ids it was built from."""

    text: str
    fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_ids", tuple(self.fact_ids))


@dataclass
class Draft:
    """A generated text (stage 6). Never send this - send what approval released."""

    lead_id: str
    paragraphs: list[Paragraph]
    signal_id: str | None = None
    subject: str | None = None
    model: str = ""
    id: str = field(default_factory=lambda: new_id("draft"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def text(self) -> str:
        """The full text, paragraphs separated by a blank line."""
        return "\n\n".join(p.text for p in self.paragraphs)

    @property
    def fact_ids(self) -> tuple[str, ...]:
        """Every cited fact id, in order of first use, without duplicates."""
        seen: dict[str, None] = {}
        for paragraph in self.paragraphs:
            for fact_id in paragraph.fact_ids:
                seen.setdefault(fact_id, None)
        return tuple(seen)


class Severity(str, Enum):
    """ERROR blocks a draft, WARNING is reported and does not block."""

    ERROR = "error"
    WARNING = "warning"


class FindingKind(str, Enum):
    """What kind of problem a verification finding describes."""

    UNSUPPORTED_NUMBER = "unsupported_number"
    UNSUPPORTED_ENTITY = "unsupported_entity"
    UNSUPPORTED_SUPERLATIVE = "unsupported_superlative"
    UNSUPPORTED_QUOTE = "unsupported_quote"
    UNKNOWN_FACT_ID = "unknown_fact_id"
    UNCITED_PARAGRAPH = "uncited_paragraph"
    UNCITED_SUPPORT = "uncited_support"
    MODEL_FLAGGED = "model_flagged"
    MODEL_UNAVAILABLE = "model_unavailable"


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem found during verification. ``message`` is shown to the user."""

    kind: FindingKind
    severity: Severity
    message: str
    excerpt: str = ""
    paragraph: int | None = None
    checker: str = ""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of the separate verification pass (stage 7)."""

    draft_id: str
    findings: tuple[Finding, ...] = ()
    checkers: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("ver"))
    checked_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "checkers", tuple(self.checkers))

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def passed(self) -> bool:
        """A draft passes exactly when no ERROR finding is present."""
        return not self.errors


class ApprovalState(str, Enum):
    """States a draft can be in on its way out (stage 8)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One state transition with its protocol entry. Approval is not a checkbox."""

    draft_id: str
    from_state: ApprovalState
    to_state: ApprovalState
    actor: str
    reason: str = ""
    id: str = field(default_factory=lambda: new_id("appr"))
    decided_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class CriterionOutcome:
    """One criterion of the rule set with the reason it did or did not apply."""

    name: str
    weight: int
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Result of the rule-based scoring (stage 4). Reproducible, not a model call."""

    lead_id: str
    total: int
    outcomes: tuple[CriterionOutcome, ...] = ()
    excluded_by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))

    @property
    def accepted(self) -> bool:
        return self.excluded_by is None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What a ``Transport`` is asked to deliver (stage 9)."""

    draft_id: str
    recipient: str
    subject: str
    body: str
    sender: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """What a ``Transport`` reports back. ``reference`` identifies the delivery."""

    transport: str
    accepted: bool
    reference: str = ""
    detail: str = ""
    sent_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class Delivery:
    """One message that actually left, together with the receipt it came back with.

    The two halves are one record because neither answers alone what stage 10 has to ask
    later. The receipt says a delivery happened and carries the reference an incoming
    reply is correlated against; the message carries the text that actually went out -
    and the honest metric of this project is a comparison against exactly that text
    (see :func:`anlass.metrics.collect.collect_outcomes`). Keeping only the receipt would
    make "was it edited by hand" unanswerable after the fact.
    """

    message: OutboundMessage
    receipt: TransportReceipt
    id: str = field(default_factory=lambda: new_id("del"))

    @property
    def draft_id(self) -> str:
        """Which draft went out here. Lives on the message, not on the receipt."""
        return self.message.draft_id


@dataclass(frozen=True, slots=True)
class FollowUp:
    """A due date for a draft that deserves a nudge. Never a message.

    There is deliberately no field here that says "sent" and no method anywhere that
    turns an entry into an outgoing message: a follow-up becoming due is a fact one can
    query, and acting on it is a separate, explicit step a person takes.
    """

    draft_id: str
    due_at: datetime
    reason: str = ""
    id: str = field(default_factory=lambda: new_id("followup"))
    created_at: datetime = field(default_factory=utcnow)


class ReplyKind(str, Enum):
    """Classification of an incoming reply (stage 10)."""

    INTERESTED = "interested"
    REJECTION = "rejection"
    AUTO_REPLY = "auto_reply"
    QUESTION = "question"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reply:
    """An incoming reply, before or after classification."""

    message_id: str
    sender: str
    subject: str
    body: str
    in_reply_to: str | None = None
    draft_id: str | None = None
    kind: ReplyKind = ReplyKind.UNKNOWN
    id: str = field(default_factory=lambda: new_id("reply"))
    received_at: datetime = field(default_factory=utcnow)


def facts_by_id(facts: Sequence[Fact]) -> dict[str, Fact]:
    """Index a fact base by id. Raises on duplicate ids - they break citations."""
    index: dict[str, Fact] = {}
    for fact in facts:
        if fact.id in index:
            raise ValueError(f"Doppelte Fakten-Kennung: '{fact.id}'.")
        index[fact.id] = fact
    return index
