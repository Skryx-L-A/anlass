"""The contract between the pipeline stages.

This file is what phase 2 builds against. Every protocol here is deliberately narrow:
one job, plain arguments, a return type from :mod:`anlass.models`, no shared state.

Conventions that hold for every implementation:

* ``name`` is a stable, lowercase identifier. It is written into provenance, findings
  and receipts, so changing it later invalidates stored records.
* Implementations raise :class:`anlass.errors.AnlassError` subclasses for expected
  failures (unreachable service, malformed data). They do not swallow errors and
  return empty results - a silent empty result is indistinguishable from "nothing
  found" and hides outages.
* Nothing here does I/O at import time, and nothing here sends anything. Only
  ``Transport.send`` leaves the machine, and only stage 8 may call it.
* All timestamps are timezone-aware UTC (:func:`anlass.models.utcnow`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator, Mapping, Protocol, Sequence, runtime_checkable

from .models import (
    ApprovalRecord,
    ApprovalState,
    Delivery,
    Draft,
    Fact,
    FieldValue,
    FollowUp,
    Lead,
    OutboundMessage,
    RawRecord,
    Reply,
    ReplyKind,
    ScoreResult,
    Signal,
    TransportReceipt,
    VerificationResult,
)

__all__ = [
    "Drafter",
    "EnrichProvider",
    "Gate",
    "LLM",
    "Scorer",
    "SignalDetector",
    "Source",
    "Store",
    "Tracker",
    "Transport",
    "Verifier",
]


@runtime_checkable
class Source(Protocol):
    """Stage 1 and 2: deliver raw records and normalise them into leads.

    Normalisation lives with the source because only the source knows the shape of its
    own records. Everything downstream sees ``Lead`` and nothing else.

    Shipped sources stay unobjectionable: open job APIs with a documented interface,
    RSS/Atom, career page fetch, file import, single URL. There is deliberately no
    module that scrapes LinkedIn - the interface is open, whoever wants it writes it.
    """

    @property
    def name(self) -> str:
        """Stable identifier, e.g. ``"rss"``. Ends up in ``Lead.source``."""

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        """Yield raw records, newest first where the source supports ordering.

        Args:
            since: Only records published/changed after this point, if the source can
                filter. Sources that cannot filter yield everything and let the caller
                drop older records.
            limit: Stop after this many records. ``None`` means no limit.

        Raises:
            anlass.errors.AnlassError: The source is unreachable or answered unusably.
        """

    def normalize(self, record: RawRecord) -> Lead:
        """Turn one raw record into a lead.

        Every field written here carries ``provider=self.name`` and a confidence that
        reflects how the value was obtained: verbatim from a structured field is 1.0,
        parsed out of prose is lower.
        """


@runtime_checkable
class EnrichProvider(Protocol):
    """Stage 3: fill missing fields of a lead. One provider of the waterfall.

    The waterfall calls providers in order and stops per field as soon as it is
    filled, so a provider must never overwrite what is already there. It returns only
    what it actually found; a field it could not determine is simply absent from the
    mapping. Guessing is what the confidence value is for, not what the field map is
    for.
    """

    @property
    def name(self) -> str:
        """Stable identifier. Written into ``FieldValue.provider``."""

    @property
    def provides(self) -> frozenset[str]:
        """Field names this provider can fill. Used to skip it when nothing is missing."""

    def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
        """Return newly found fields for ``lead``.

        Args:
            lead: The lead, read-only. Do not mutate it; the waterfall writes.
            missing: The subset of :attr:`provides` that is still empty.

        Returns:
            A mapping field name to ``FieldValue``. May be empty. Keys outside
            ``missing`` are ignored by the waterfall.
        """


@runtime_checkable
class Scorer(Protocol):
    """Stage 4: score a lead against the user's criteria file.

    A language model may extract fields, but it must not score. The score is a rule
    evaluation over the criteria file, therefore reproducible, explainable and
    testable - for every rejection the criterion that caused it can be named.
    """

    @property
    def name(self) -> str:
        """Stable identifier of the rule set (e.g. the criteria file name)."""

    def score(self, lead: Lead) -> ScoreResult:
        """Evaluate every criterion and return the total plus a reason per criterion.

        Implementations set ``excluded_by`` to the name of the first exclusion rule
        that applied. A lead below the minimum score is excluded here, not later -
        that is the rule against mass applications, and it is not switchable.
        """


@runtime_checkable
class SignalDetector(Protocol):
    """Stage 5: find the quotable occasion for contacting this recipient now."""

    @property
    def name(self) -> str:
        """Stable identifier. Written into ``Signal.detector``."""

    def detect(self, lead: Lead) -> list[Signal]:
        """Return every occasion found, strongest first, or an empty list.

        An empty list is a valid and frequent answer and means: do not contact. Every
        returned signal quotes source text verbatim; a paraphrase is not a signal.
        """


@runtime_checkable
class Drafter(Protocol):
    """Stage 6: generate a draft from facts, lead and signal - from nothing else."""

    @property
    def name(self) -> str:
        """Stable identifier, usually including the model name."""

    def draft(
        self,
        lead: Lead,
        signal: Signal | None,
        facts: Sequence[Fact],
        voice: str = "",
    ) -> Draft:
        """Produce a draft whose paragraphs each name the fact ids they used.

        Args:
            lead: The recipient.
            signal: The occasion. ``None`` is allowed for a first contact without one,
                but stage 8 rejects such drafts - rule 1 has no exception.
            facts: The complete, already filtered fact base the draft may use. The
                implementation passes nothing else to the model.
            voice: Free German prose describing how the user sounds and what they
                never write.

        Returns:
            A draft in which every ``Paragraph.fact_ids`` entry exists in ``facts``.

        Raises:
            anlass.errors.DraftError: The model returned an unusable structure or
                cited a fact id that does not exist.
        """


@runtime_checkable
class Verifier(Protocol):
    """Stage 7: hold the finished text against the fact base. Separate pass.

    Separate means: the verifier does not see how the draft came to be, gets its own
    view of the fact base and may use a different (usually stronger) model. A verifier
    that runs inside generation verifies its own opinion.
    """

    @property
    def name(self) -> str:
        """Stable identifier. Written into ``Finding.checker``."""

    def verify(
        self,
        draft: Draft,
        facts: Sequence[Fact],
        *,
        lead: Lead | None = None,
        signal: Signal | None = None,
    ) -> VerificationResult:
        """Find every factual statement that does not point at a fact.

        ``lead`` and ``signal`` are legitimate grounding as well: their values come
        from the source, not from the model. They are optional so the verifier can be
        run over a text alone.

        Returns:
            A result whose ``passed`` is ``False`` as soon as one ERROR finding is
            present. Implementations never raise because of a bad draft - a bad draft
            is a finding, not an exception.
        """


@runtime_checkable
class Gate(Protocol):
    """Stage 8: release as a recorded transition, guarded by the anti-mass rule.

    Two methods, and the split between them is the point. ``check`` is the dry run:
    it names everything that speaks against releasing this draft and changes nothing.
    ``approve`` is the only place a release is written down, and it re-runs ``check``
    itself instead of trusting that a caller did - a gate that believes its caller is
    not a gate.

    An implementation looks a lead's score up in the :class:`Store` rather than taking
    the caller's word for it; ``score`` below exists for the case where the run has a
    fresh result in hand and no store to read it from, not as a way to hand the gate a
    number it could have checked itself.

    There is deliberately no parameter, on either method, that skips a check.
    """

    @property
    def name(self) -> str:
        """Stable identifier. Written into the run protocol."""

    def check(
        self,
        draft: Draft,
        *,
        signal: Signal | None = None,
        score: ScoreResult | None = None,
        verification: VerificationResult | None = None,
    ) -> list[str]:
        """German reasons that speak against releasing this draft.

        An empty list means nothing does. Every argument is optional because the gate
        can read signal, score and verification back out of the store; passing them is
        an offer, not a requirement, and never a way to override what is stored.
        """

    def approve(self, draft: Draft, *, actor: str, reason: str = "") -> ApprovalRecord:
        """Record the transition to APPROVED.

        Raises:
            anlass.errors.AnlassError: An objection from :meth:`check` applies, or the
                transition is not allowed from the draft's current state.
        """


@runtime_checkable
class Transport(Protocol):
    """Stage 9: the pluggable way out (SMTP, file, clipboard, webhook).

    The only place in this package that talks to the outside world on a user's behalf.
    Implementations must not send anything from ``preflight``.
    """

    @property
    def name(self) -> str:
        """Stable identifier. Written into ``TransportReceipt.transport``."""

    def preflight(self, message: OutboundMessage) -> list[str]:
        """Check deliverability without sending. Returns German problem descriptions.

        An empty list means "nothing speaks against it". Checked are the enforced
        rules of the large providers: SPF, DKIM and DMARC on the sender domain, an
        unsubscribe option for series sends, complaint rate below 0.3 percent, bounces
        below 2 percent, volume ramp for fresh domains. The check is built, the warmup
        infrastructure is not - that is a product of its own.
        """

    def send(self, message: OutboundMessage) -> TransportReceipt:
        """Deliver the message. Only called after approval.

        Raises:
            anlass.errors.AnlassError: Delivery failed in a way the caller must see.
                A rejected but understood delivery is a receipt with
                ``accepted=False``, not an exception.
        """


@runtime_checkable
class Tracker(Protocol):
    """Stage 10: read replies back in and classify them."""

    @property
    def name(self) -> str:
        """Stable identifier."""

    def poll(self, *, since: datetime | None = None, limit: int | None = None) -> list[Reply]:
        """Fetch new replies. Correlation to a draft is best effort.

        Implementations set ``Reply.draft_id`` when they can match a reply to a sent
        message (message id, reference header, recipient address), and leave it
        ``None`` otherwise rather than guessing.
        """

    def classify(self, reply: Reply) -> ReplyKind:
        """Classify one reply. Pure function over the reply, no side effects."""


@runtime_checkable
class Store(Protocol):
    """Persistence for everything the pipeline produces.

    Writes are idempotent by id: saving the same object twice must not create a second
    row. Reads return ``None`` for a missing id and never raise for it.
    """

    def migrate(self) -> None:
        """Create or upgrade the schema. Safe to call on every start."""

    def save_lead(self, lead: Lead) -> None:
        """Store lead and all its fields including provenance."""

    def get_lead(self, lead_id: str) -> Lead | None: ...

    def list_leads(self, *, limit: int | None = None) -> list[Lead]:
        """Newest first."""

    def lead_by_ref(self, source: str, source_ref: str) -> Lead | None:
        """The lead this source already produced for this reference, or ``None``.

        Without it every run of stage 1 creates a second lead for a posting that is
        already known: sources mint a fresh id each time and ``save_lead`` writes by id.
        The tool is meant to run daily against the same sources, so that is the normal
        case, not an edge case - and a duplicate lead is drafted again and spends the
        daily cap a second time.
        """

    def save_signal(self, signal: Signal) -> None: ...

    def signals_for_lead(self, lead_id: str) -> list[Signal]: ...

    def signal_by_id(self, signal_id: str) -> Signal | None:
        """The occasion with this id, or ``None``.

        A draft carries the id of the occasion it was written against
        (:attr:`~anlass.models.Draft.signal_id`), and that is the only occasion it may
        be measured against. Every run adds a further occasion to the lead, so looking
        one up by lead means looking up the oldest - which is a different sentence than
        the one the draft answered (Q13: 20 of 26 points instead of
        24, plus a revision note quoting a sentence the draft never saw).
        """

    def save_score(self, result: ScoreResult) -> None:
        """Store the score of a lead, replacing an earlier one for the same lead.

        A :class:`~anlass.models.ScoreResult` has no id of its own; the lead it belongs
        to is the key. Re-scoring therefore overwrites rather than appending: a lead has
        one current score, and the gate must not have to guess which of several is meant.
        """

    def latest_score(self, lead_id: str) -> ScoreResult | None:
        """The stored score of a lead, or ``None`` if it was never scored.

        Stage 8 reads its own score through this method instead of taking one from the
        caller. Without it the minimum-score rule would depend on what the caller says
        the score was, which is exactly the bypass that rule exists to deny.
        """

    def save_draft(self, draft: Draft) -> None:
        """Store the draft including the fact ids per paragraph.

        The first save of a draft id also records its text as the generated version
        (:meth:`generated_text`). Later saves update the draft and leave that version
        alone - it is the yardstick the honest metric measures against, so it must not
        move when somebody corrects the draft.
        """

    def get_draft(self, draft_id: str) -> Draft | None: ...

    def list_drafts(self, *, limit: int | None = None) -> list[Draft]:
        """Newest first. What the metrics run over."""

    def generated_text(self, draft_id: str) -> str | None:
        """The draft's text as the drafter produced it, or ``None`` if it is unknown.

        This is what makes "was it corrected by hand" answerable at all: a text
        comparison against the generated version is the cheapest honest test, and it
        needs the generated version to survive the correction.
        """

    def save_verification(self, result: VerificationResult) -> None: ...

    def latest_verification(self, draft_id: str) -> VerificationResult | None: ...

    def save_approval(self, record: ApprovalRecord) -> None:
        """Append one state transition. History is append-only."""

    def approvals_for_draft(self, draft_id: str) -> list[ApprovalRecord]:
        """Oldest first - this is the protocol of the transitions."""

    def approvals_since(
        self, start: datetime, *, to_state: ApprovalState | None = None
    ) -> list[ApprovalRecord]:
        """Transitions at or after ``start``, across all drafts, newest first.

        This is what makes the daily cap survive a restart: counting today's releases
        needs a view across drafts, and :meth:`approvals_for_draft` cannot give one.
        Without it the count lives in process memory and a restart sets it to zero,
        which quietly removes one of the three rules the tool rests on.

        Args:
            to_state: Only transitions into this state. Counting sends means counting
                ``APPROVED``: a draft that is approved and then sent leaves two records,
                and both would count as two releases.
        """

    def current_state(self, draft_id: str) -> ApprovalState:
        """State after the last transition, ``PENDING`` if there was none."""

    def save_delivery(self, delivery: Delivery) -> None:
        """Record that a message left, with the text that left and the receipt.

        The approval history already says a draft reached ``SENT``; it does not say what
        was in the message or where it went. Both are needed afterwards - the reference
        to correlate a reply against, the text to compare against the generated version.
        Idempotent by ``Delivery.id``.
        """

    def list_deliveries(
        self, *, draft_id: str | None = None, limit: int | None = None
    ) -> list[Delivery]:
        """What left, newest first, optionally for one draft only."""

    def save_reply(self, reply: Reply) -> None:
        """Store one incoming reply, keyed by its ``message_id``.

        Keyed by the message id and not by ``Reply.id`` because polling is repeatable:
        the same mail read twice is the same reply, and a second row would count as a
        second answer in every rate below.

        The stored text is data, never an instruction - a reply that says "ignore your
        rules" is a text to classify and nothing else.
        """

    def list_replies(
        self, *, draft_id: str | None = None, limit: int | None = None
    ) -> list[Reply]:
        """Replies, newest first, optionally only those correlated to one draft."""

    def save_follow_up(self, entry: FollowUp) -> None:
        """One open follow-up per draft. Saving a second one replaces the first."""

    def list_follow_ups(
        self, *, draft_id: str | None = None, due_by: datetime | None = None
    ) -> list[FollowUp]:
        """Scheduled follow-ups, earliest due date first.

        Args:
            due_by: Only entries due at or before this point - "what is due now".
        """


@runtime_checkable
class LLM(Protocol):
    """A language model behind one narrow interface.

    Three provider types implement it (local via Ollama, cloud via API key, cloud via
    an existing subscription driven headless), so switching is one configuration line.
    Implementations are stateless between calls and never log a key.
    """

    @property
    def name(self) -> str:
        """Stable identifier including the model, e.g. ``"ollama:qwen3:8b"``."""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Return the model's answer as text.

        Raises:
            anlass.errors.LLMError: Provider unreachable, refused, or answered with
                something that is not text.
        """

    def available(self) -> bool:
        """Cheap reachability check for ``anlass init``. Never raises.

        A configuration that only fails at the first real use is not a configuration.
        """
