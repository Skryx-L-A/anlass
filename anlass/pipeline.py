"""The chain that connects the ten stages, and the record of what it did.

Two properties carry this module.

**It is programmed against the protocols, not against implementations.** Every stage is
a slot typed by :mod:`anlass.interfaces`. A slot that is empty is not an error and not a
silent skip: the run records the stage as ``MISSING`` and says so in German. That is how
a half-built chain stays usable and honest at the same time - the parts that exist run,
the parts that do not are named.

**A run is traceable.** For every result the stage that produced it can be named: each
stage appends a :class:`Step` with its own ``name``, what it produced and why it stopped
if it did. :meth:`Run.producer_of` answers "where does this come from" for any id, which
is the same question the verification pass asks about a sentence, one level up.

What this module deliberately does not do
-----------------------------------------

It does not send. :meth:`Pipeline.run_lead` ends at stage 8 at the latest; delivery is
:meth:`Pipeline.deliver` and needs an approval record that stage 8 wrote. As long as
stage 8 is not plugged in, nothing can be released - the safe direction, and the reason
a missing gate is not treated as "no objection".

It also does not decide *which* implementation fills a slot. Assembling a chain from a
user's configuration and profile is :mod:`anlass.wiring`; keeping that out of here is
what lets a test run the whole chain against stand-ins it wrote itself.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterator, Sequence

from .critique import Critic, Critique
from .errors import AnlassError
from .interfaces import (
    Drafter,
    EnrichProvider,
    Gate,
    Scorer,
    SignalDetector,
    Source,
    Store,
    Tracker,
    Transport,
    Verifier,
)
from .models import (
    ApprovalRecord,
    ApprovalState,
    Delivery,
    Draft,
    Fact,
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
    utcnow,
)
from .track.followup import DEFAULT_FOLLOW_UP_AFTER

#: Re-exported so ``from anlass.pipeline import Gate`` keeps working. The protocol
#: itself moved to :mod:`anlass.interfaces`, where the rest of the contract lives -
#: stage 8 had no entry there while phase 2 was running, and this is that gap closed.
__all__ = [
    "Gate",
    "MAX_FORM_RETRIES",
    "Pipeline",
    "RevisionRound",
    "Run",
    "Stage",
    "StepStatus",
    "Step",
]

#: How often a round that broke a countable rule of the posting may be retried without
#: costing a revision. Two, and bounded on purpose: the budget is there so a draft is
#: not polished forever, and a round that never got past a formality was not polished at
#: all - but a model that misses the same sentence cap three times running will not hit
#: it on the fourth, and every retry is a model call.
MAX_FORM_RETRIES = 2


class Stage(str, Enum):
    """The stages of the plan, by the number they carry there.

    ``CRITIQUE`` is not an eleventh stage. It is the second half of stage 6/7: the
    quality measure that runs after the grounding check and decides whether the draft
    is rewritten. It has its own entry because a run has to be able to say what it
    measured and how often it revised.
    """

    SOURCE = "source"
    ENRICH = "enrich"
    SCORE = "score"
    SIGNAL = "signal"
    DRAFT = "draft"
    VERIFY = "verify"
    CRITIQUE = "critique"
    GATE = "gate"
    SEND = "send"
    TRACK = "track"

    @property
    def label(self) -> str:
        """German name of the stage, for output a user reads."""
        return _STAGE_LABELS[self]


_STAGE_LABELS = {
    Stage.SOURCE: "Quelle",
    Stage.ENRICH: "Anreicherung",
    Stage.SCORE: "Bewertung",
    Stage.SIGNAL: "Anlass",
    Stage.DRAFT: "Entwurf",
    Stage.VERIFY: "Pruefung",
    Stage.CRITIQUE: "Guete",
    Stage.GATE: "Freigabe",
    Stage.SEND: "Versand",
    Stage.TRACK: "Rueckkanal",
}


class StepStatus(str, Enum):
    """How a stage ended.

    ``MISSING`` and ``STOPPED`` are both normal outcomes and mean different things:
    ``MISSING`` is "this stage is not built yet", ``STOPPED`` is "this stage ran and
    decided the run ends here". Collapsing them would hide which of the two happened.
    """

    OK = "ok"
    MISSING = "missing"
    SKIPPED = "skipped"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Step:
    """One stage of one run: who ran, what came out, and why it ended that way."""

    stage: Stage
    status: StepStatus
    detail: str
    implementation: str = ""
    produced: tuple[str, ...] = ()
    at: datetime = field(default_factory=utcnow)

    def line(self) -> str:
        """One German line for the terminal."""
        by = f" [{self.implementation}]" if self.implementation else ""
        return f"{self.stage.label:14} {self.status.value:8}{by} {self.detail}"


@dataclass(frozen=True, slots=True)
class RevisionRound:
    """One attempt at a draft: what came out, what it scored, what it was told.

    Every round is kept, including the discarded one. Without them the loop is a black
    box and nobody can read afterwards how a draft got better - or that it got worse.

    Args:
        number: This attempt, counted from one.
        total: How many attempts are allowed at most in this run.
        draft: What the generator produced in this round.
        verification: Stage 7 on this draft.
        critique: The quality measure on this draft.
        notes: The revision notes this round was **given**. Empty in round one.
        discarded_because: Empty while the round counts. Filled with the German reason
            when it does not - it scored worse than the round before it, or it came back
            unchanged. Such a round is recorded and not used: a revision that harms is
            not repeated three times, and one that changes nothing will not change
            anything on the fourth try either.
        rejected_because: Empty while the generator accepted its own answer. Filled with
            the German reason when it did not: too many sentences for the posting, the
            wrong form of address, too many paragraphs. Such a round still **counts** -
            the text exists and is measured like any other. It only loses against every
            round that broke no rule, and it is handed over with the violation named.
    """

    number: int
    total: int
    draft: Draft
    verification: VerificationResult | None = None
    critique: Critique | None = None
    notes: tuple[str, ...] = ()
    discarded_because: str = ""
    rejected_because: str = ""

    @property
    def discarded(self) -> bool:
        return bool(self.discarded_because)

    @property
    def rejected(self) -> bool:
        return bool(self.rejected_because)

    def line(self) -> str:
        """One German line for the terminal."""
        score = "" if self.critique is None else f", {self.critique.earned} von {self.critique.possible} Punkten"
        tail = f" - verworfen, {self.discarded_because}" if self.discarded else ""
        if self.rejected:
            tail += f" - haelt eine harte Vorgabe nicht ein: {self.rejected_because}"
        return f"Entwurf Runde {self.number}/{self.total}{score}{tail}"


@dataclass
class Run:
    """Everything one lead produced, plus who produced it."""

    lead: Lead | None = None
    steps: list[Step] = field(default_factory=list)
    score: ScoreResult | None = None
    signal: Signal | None = None
    draft: Draft | None = None
    verification: VerificationResult | None = None
    critique: Critique | None = None
    rounds: list[RevisionRound] = field(default_factory=list)
    approval: ApprovalRecord | None = None
    receipt: TransportReceipt | None = None

    @property
    def stopped_at(self) -> Step | None:
        """The step that ended the run, or ``None`` if it ran through."""
        for step in self.steps:
            if step.status in (StepStatus.STOPPED, StepStatus.FAILED):
                return step
        return None

    @property
    def missing_stages(self) -> tuple[Stage, ...]:
        """Stages that are not plugged in yet, in the order they would have run."""
        return tuple(s.stage for s in self.steps if s.status is StepStatus.MISSING)

    def step(self, stage: Stage) -> Step | None:
        """The step of ``stage``, or ``None`` if the run never got there."""
        for entry in self.steps:
            if entry.stage is stage:
                return entry
        return None

    def producer_of(self, identifier: str) -> Step | None:
        """Which stage produced the object with this id.

        This is the traceability the plan asks for: for every result of a run the
        producing stage can be named, without keeping a second bookkeeping structure.
        """
        for entry in self.steps:
            if identifier in entry.produced:
                return entry
        return None

    def trace(self) -> list[str]:
        """The whole run as German lines, in order, with the revision rounds shown.

        The rounds hang under the draft step and are indented, because they are not
        stages of their own. Showing them is not decoration: a loop that revises a draft
        three times and nobody sees it is a black box.
        """
        lines: list[str] = []
        for step in self.steps:
            lines.append(step.line())
            if step.stage is Stage.DRAFT:
                lines.extend("  " + entry.line() for entry in self.rounds)
        return lines


@dataclass
class Pipeline:
    """The ten stages as slots. Empty slots stay open, they do not fail the run.

    Args:
        facts: The fact base a draft may draw on. Nothing else is passed to the model.
        voice: Free German prose describing how the sender sounds.
        source: Stages 1 and 2.
        enrichers: Stage 3, in waterfall order. The first provider that fills a field
            keeps it - :meth:`anlass.models.Lead.set` enforces that, not this loop.
        scorer: Stage 4.
        detector: Stage 5.
        drafter: Stage 6.
        verifier: Stage 7.
        critic: The quality measure that runs after stage 7 and decides whether the
            draft goes back for another round. An empty slot means nobody measures
            whether the draft is any good - the state the tool was in until phase 5.
        max_revisions: How often a draft may be rewritten. Only the *revisions* are
            counted here, so the default of three means at most four attempts. After
            that the best attempt goes to the person with its open points.
        gate: Stage 8. Without it nothing is released, on purpose.
        transport: Stage 9. Only :meth:`deliver` ever touches it.
        tracker: Stage 10. Read by :meth:`poll_replies`, never by a lead's own run - a
            reply arrives days after the run that caused it, so it cannot be a step of
            that run.
        store: Persistence. Optional; without it a run is not written down.
        actor: Who is recorded as the acting person in approval records.
    """

    facts: Sequence[Fact] = ()
    voice: str = ""
    source: Source | None = None
    enrichers: Sequence[EnrichProvider] = ()
    scorer: Scorer | None = None
    detector: SignalDetector | None = None
    drafter: Drafter | None = None
    verifier: Verifier | None = None
    critic: Critic | None = None
    max_revisions: int = 3
    gate: Gate | None = None
    transport: Transport | None = None
    tracker: Tracker | None = None
    store: Store | None = None
    actor: str = "nutzer"

    # ------------------------------------------------------------------ stages 1+2

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> list[Lead]:
        """Read records from the source and normalise them into leads.

        Raises:
            anlass.errors.AnlassError: No source is plugged in, or the source failed.
        """
        if self.source is None:
            raise AnlassError(
                "Es ist keine Quelle eingesetzt. Ohne Stufe 1 gibt es nichts einzulesen."
            )
        leads: list[Lead] = []
        for record in self.source.fetch(since=since, limit=limit):
            lead = self.source.normalize(record)
            # A posting already read stays the lead it was. Sources mint a fresh id per
            # run and the store writes by id, so without this every daily run would file
            # the same posting again, draft it again and spend the daily cap a second
            # time. Fields are carried over onto the known lead, where Lead.set keeps the
            # first provider that filled one - so re-reading costs nothing and a genuinely
            # new field still lands.
            known = self._known_lead(lead)
            if known is not None:
                for name, value in lead.fields.items():
                    known.set(name, value)
                if lead.text and not known.text:
                    known.text = lead.text
                lead = known
            self._save_lead(lead)
            leads.append(lead)
        return leads

    def _known_lead(self, lead: Lead) -> Lead | None:
        """The lead this source produced for the same reference before, if any."""
        if self.store is None or not lead.source_ref:
            return None
        lookup = getattr(self.store, "lead_by_ref", None)
        if lookup is None:
            return None
        return lookup(lead.source, lead.source_ref)

    def fetch_runs(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> Iterator[Run]:
        """Read from the source and run the chain for every lead."""
        for lead in self.fetch(since=since, limit=limit):
            yield self.run_lead(lead)

    # -------------------------------------------------------------------- the chain

    def run_lead(self, lead: Lead, *, record: RawRecord | None = None) -> Run:
        """Run stages 3 to 8 for one lead. Never sends - that is :meth:`deliver`.

        The run ends early, with a step that says why, when the scorer excludes the
        lead, when no occasion is found, when a needed stage is not plugged in, or when
        verification produces an error finding for every attempt.
        """
        run = Run(lead=lead)
        origin = self.source.name if self.source is not None else (lead.source or "unbekannt")
        run.steps.append(
            Step(
                stage=Stage.SOURCE,
                status=StepStatus.OK,
                detail=f"Lead aus '{lead.source_ref or origin}' uebernommen.",
                implementation=origin,
                produced=(lead.id,),
            )
        )

        if not self._enrich(run, lead):
            return run
        if not self._score(run, lead):
            return run
        if not self._detect(run, lead):
            return run
        if not self._write_draft(run, lead):
            return run
        self._gate(run)
        return run

    def score_lead(self, lead: Lead) -> Run:
        """Stage 4 alone for a lead that is already in the store.

        What ``anlass score`` runs. Separate from :meth:`run_lead` because re-scoring
        after a change to the criteria file must not re-fetch, re-enrich or re-draft
        anything - the point of the exercise is to see what the new rules make of the
        leads that are already there.
        """
        run = Run(lead=lead)
        run.steps.append(
            Step(
                stage=Stage.SOURCE,
                status=StepStatus.SKIPPED,
                detail=f"Lead aus dem Speicher: {lead.source_ref or lead.source}.",
                implementation=lead.source,
                produced=(lead.id,),
            )
        )
        self._score(run, lead)
        return run

    def draft_lead(
        self,
        lead: Lead,
        *,
        signal: Signal | None = None,
        score: ScoreResult | None = None,
    ) -> Run:
        """Stages 6 to 8 for a lead that already went through the earlier stages.

        This is what ``anlass draft`` runs: the lead comes out of the store, so reading
        and enriching it again would only produce a second, differently dated copy.
        """
        run = Run(lead=lead, signal=signal, score=score)
        run.steps.append(
            Step(
                stage=Stage.SOURCE,
                status=StepStatus.SKIPPED,
                detail=f"Lead aus dem Speicher: {lead.source_ref or lead.source}.",
                implementation=lead.source,
                produced=(lead.id,),
            )
        )
        if signal is None:
            run.steps.append(
                Step(
                    stage=Stage.SIGNAL,
                    status=StepStatus.MISSING,
                    detail=(
                        "Zu diesem Lead ist kein Anlass gespeichert. Der Entwurf entsteht, "
                        "die Freigabe weist ihn zurueck - Regel 1 hat keine Ausnahme."
                    ),
                )
            )
        else:
            run.steps.append(
                Step(
                    stage=Stage.SIGNAL,
                    status=StepStatus.SKIPPED,
                    detail=f"Anlass '{signal.kind}' aus dem Speicher uebernommen.",
                    implementation=signal.detector,
                    produced=(signal.id,),
                )
            )
        if not self._write_draft(run, lead):
            return run
        self._gate(run)
        return run

    def _enrich(self, run: Run, lead: Lead) -> bool:
        if not self.enrichers:
            run.steps.append(
                Step(
                    stage=Stage.ENRICH,
                    status=StepStatus.MISSING,
                    detail="Keine Anreicherung eingesetzt. Der Lead bleibt so, wie die Quelle ihn lieferte.",
                )
            )
            return True
        filled: list[str] = []
        failed: list[str] = []
        for provider in self.enrichers:
            missing = lead.missing(provider.provides)
            if not missing:
                continue
            try:
                found = provider.enrich(lead, missing)
            except AnlassError as exc:
                # A provider that fails costs its fields, not the run. An unreachable
                # page or a model that answers unusably is ordinary, and the safe
                # consequence is a thinner lead: the fields stay empty, the score at
                # stage 4 comes out lower, and the lead may be excluded there - visibly,
                # by the rule. Ending the run here instead would let one flaky provider
                # stop everything, which is neither safer nor more honest. The failure
                # is named in the step, never swallowed.
                failed.append(f"{provider.name} ({exc})")
                continue
            for name, value in found.items():
                if name in missing and lead.set(name, value):
                    filled.append(f"{name} ({value.provider})")
        self._save_lead(lead)
        parts: list[str] = []
        if filled:
            parts.append("Neu gefuellte Felder mit Herkunft: " + ", ".join(filled))
        else:
            parts.append("Kein Feld war offen oder keines liess sich fuellen.")
        if failed:
            parts.append(
                "Ohne Ergebnis geblieben, die Felder bleiben leer: " + "; ".join(failed)
            )
        run.steps.append(
            Step(
                stage=Stage.ENRICH,
                status=StepStatus.OK,
                detail=" ".join(parts),
                implementation=", ".join(p.name for p in self.enrichers),
                produced=tuple(name.split(" ")[0] for name in filled),
            )
        )
        return True

    def _score(self, run: Run, lead: Lead) -> bool:
        if self.scorer is None:
            run.steps.append(
                Step(
                    stage=Stage.SCORE,
                    status=StepStatus.MISSING,
                    detail=(
                        "Keine Bewertung eingesetzt. Ohne Stufe 4 gibt es keine Punktzahl "
                        "und damit auch keine Mindestpunktzahl, die greifen koennte."
                    ),
                )
            )
            return True
        result = self.scorer.score(lead)
        run.score = result
        if self.store is not None:
            # Stage 8 reads the score back out of the store rather than being handed
            # one. Writing it here, before the run can end, is what makes that possible
            # for an excluded lead too - "was scored and rejected" and "was never
            # scored" are different answers and the gate must be able to tell them apart.
            self.store.save_score(result)
        if not result.accepted:
            run.steps.append(
                Step(
                    stage=Stage.SCORE,
                    status=StepStatus.STOPPED,
                    detail=(
                        f"Punktzahl {result.total}, ausgeschlossen durch "
                        f"'{result.excluded_by}'. Hier endet der Durchlauf."
                    ),
                    implementation=self.scorer.name,
                )
            )
            return False
        run.steps.append(
            Step(
                stage=Stage.SCORE,
                status=StepStatus.OK,
                detail=f"Punktzahl {result.total} aus {len(result.outcomes)} Kriterien.",
                implementation=self.scorer.name,
            )
        )
        return True

    def _detect(self, run: Run, lead: Lead) -> bool:
        if self.detector is None:
            run.steps.append(
                Step(
                    stage=Stage.SIGNAL,
                    status=StepStatus.MISSING,
                    detail=(
                        "Keine Anlass-Erkennung eingesetzt. Ein Entwurf ohne Anlass kann "
                        "entstehen, aber die Freigabe weist ihn zurueck - Regel 1 hat keine Ausnahme."
                    ),
                )
            )
            return True
        signals = self.detector.detect(lead)
        if not signals:
            run.steps.append(
                Step(
                    stage=Stage.SIGNAL,
                    status=StepStatus.STOPPED,
                    detail="Kein zitierbarer Anlass gefunden. Also wird nicht angeschrieben.",
                    implementation=self.detector.name,
                )
            )
            return False
        run.signal = signals[0]
        if self.store is not None:
            self.store.save_signal(run.signal)
        run.steps.append(
            Step(
                stage=Stage.SIGNAL,
                status=StepStatus.OK,
                detail=f"Anlass '{run.signal.kind}': \"{run.signal.quote[:80]}\"",
                implementation=self.detector.name,
                produced=(run.signal.id,),
            )
        )
        return True

    # ------------------------------------------------------------------ stages 6+7

    def _write_draft(self, run: Run, lead: Lead) -> bool:
        """Draft, check, measure - and rewrite until nothing is open or the budget ends.

        Returns whether the run may go on to the gate. It may not when there is no
        draft at all and not when the best attempt still contains something unsupported:
        an unverified letter is not released, and the loop does not change that rule, it
        only gives the generator a chance to fix the finding first.
        """
        if self.drafter is None:
            run.steps.append(
                Step(
                    stage=Stage.DRAFT,
                    status=StepStatus.MISSING,
                    detail="Keine Entwurfsstufe eingesetzt. Hier endet der Durchlauf.",
                )
            )
            return False
        if not self.facts:
            run.steps.append(
                Step(
                    stage=Stage.DRAFT,
                    status=StepStatus.STOPPED,
                    detail="Die Faktenbasis ist leer. Ohne Belege wird kein Entwurf erzeugt.",
                    implementation=self.drafter.name,
                )
            )
            return False

        rounds, failures, stopped_because = self._revision_loop(run, lead)
        run.rounds = rounds
        kept = [entry for entry in rounds if not entry.discarded]
        if not kept:
            # Only when not one attempt produced a text. A generator that refused its
            # own answer produced one, and it is in ``kept`` - see _revision_loop.
            run.steps.append(
                Step(
                    stage=Stage.DRAFT,
                    status=StepStatus.FAILED,
                    detail="Der Entwurf kam nicht zustande: " + " ".join(failures),
                    implementation=self.drafter.name,
                )
            )
            return False

        best = max(kept, key=_rank)
        run.draft = best.draft
        run.verification = best.verification
        run.critique = best.critique
        taken = (
            ""
            if len(kept) == 1
            else f" Uebernommen wird Runde {best.number}, der beste Versuch."
        )
        if best.rejected:
            taken += (
                " Kein Versuch hielt jede harte Vorgabe ein; dieser bleibt der beste und "
                f"geht mit seinem offenen Punkt an dich: {best.rejected_because}"
            )
        if failures:
            count = "Eine Runde scheiterte" if len(failures) == 1 else f"{len(failures)} Runden scheiterten"
            taken += f" {count}: " + " ".join(failures)
        if stopped_because:
            taken += f" {stopped_because}"
        run.steps.append(
            Step(
                stage=Stage.DRAFT,
                status=StepStatus.OK,
                detail=(
                    f"{len(best.draft.paragraphs)} Absaetze, belegt mit "
                    f"{len(best.draft.fact_ids)} Kennungen." + taken
                ),
                implementation=best.draft.model or self.drafter.name,
                produced=(best.draft.id,),
            )
        )
        passed = self._report_verification(run, best)
        self._report_critique(run, best)
        return passed

    def _revision_loop(
        self, run: Run, lead: Lead
    ) -> tuple[list[RevisionRound], list[str], str]:
        """Every attempt, in order. Returns rounds, failed attempts, and why it stopped.

        Five things end the loop: full marks, the budget, a round that came out worse
        than the one before it, a round that came back unchanged, and a round that
        failed the same way as the round before it. The last three are not caution but
        arithmetic - a revision that lowered the score, moved nothing, or broke exactly
        the rule it was just told about will not do better by being repeated, and each
        round costs a model call. Measured on 29.07.2026: five of six rounds against the
        Talwerk posting broke the same two-sentence bound after being told about it five
        times (Q19). Those were five model calls spent on an answer that was not coming.

        **A round the generator rejected is not one of them, and it is not lost
        either.** Stage 6 refuses its own answer when it broke a countable rule of the
        posting (too many sentences, too many paragraphs, the wrong form of address),
        and that refusal does two things here. It goes back as a revision note, because
        it is the best one there is: concrete, checked by machine, and it names exactly
        what to change. And the refused text is **kept as a round**, measured like any
        other, because it exists: measured on 29.07.2026 the Nordlicht posting produced
        four attempts, all of them over the three-sentence cap, and the run ended with
        no draft at all. Four model calls, four texts, nothing to look at and nothing to
        correct by hand. A round that broke a rule loses to every round that did not
        (see :func:`_rank`), so it only ever wins when nothing better exists - and then
        it is exactly what should be handed over, with the violation named.

        **A rejected round does not cost a revision, up to**
        :data:`MAX_FORM_RETRIES`. The budget exists so a draft is not polished forever;
        a round that failed on a countable formality never got as far as being polished,
        and spending a revision on it trades a quality round for a formality. The free
        retries are bounded because a model that misses the same cap three times in a
        row will not hit it on the fourth: the total number of model calls is at most
        ``allowed + MAX_FORM_RETRIES``. Rounds that produced no text at all (unusable
        JSON, a call that timed out) get no free retry - there is nothing to learn from
        and no text to keep.

        A generator that takes no notes gets one attempt throughout - handing it the same
        input again would produce the same failure - but its refused text is kept exactly
        the same way. The seam decides whether there is a second round, not whether the
        first one is worth anything.
        """
        assert self.drafter is not None
        takes_notes = _accepts_revision_notes(self.drafter)
        allowed = 1 + (max(self.max_revisions, 0) if takes_notes else 0)
        free_retries = MAX_FORM_RETRIES if takes_notes else 0
        total = allowed + free_retries
        rounds: list[RevisionRound] = []
        failures: list[str] = []
        critique_notes: tuple[str, ...] = ()
        rejected: str = ""
        stopped_because = ""
        last_violation = ""
        number = 0
        spent = 0
        while spent < allowed:
            number += 1
            # The rejection leads, because it is the reason this round exists at all;
            # the open criteria of the attempt it rejected stand behind it.
            notes = ((rejected,) if rejected else ()) + critique_notes
            current: RevisionRound | None = None
            repeated = False
            try:
                draft = self._ask_drafter(lead, run.signal, notes, takes_notes)
            except AnlassError as exc:
                failures.append(str(exc))
                refused = getattr(exc, "draft", None)
                if refused is not None and free_retries > 0:
                    free_retries -= 1
                else:
                    spent += 1
                violation = _violation_key(exc)
                repeated = bool(violation) and violation == last_violation
                last_violation = violation
                rejected = _rejection_note(str(exc))
                if refused is not None:
                    current = replace(
                        self._measure(refused, run, lead, number, total, notes),
                        rejected_because=str(exc),
                    )
            else:
                spent += 1
                rejected = ""
                last_violation = ""
                current = self._measure(draft, run, lead, number, total, notes)
                if rounds and not rounds[-1].rejected and _rank(current) < _rank(rounds[-1]):
                    # Only against a round that stands: a rejected round is not a state
                    # the loop was aiming for, so being "worse" than one says nothing.
                    rounds.append(replace(current, discarded_because="schlechter als die Runde davor"))
                    break
            if current is None:  # nothing came back at all - nothing to keep, try again
                continue
            if rounds and current.draft.text == rounds[-1].draft.text:
                rounds.append(
                    replace(current, discarded_because="unveraendert gegenueber der Runde davor")
                )
                break
            rounds.append(current)
            if repeated:
                # The round is kept - it is a text, and it may be the best one there is.
                # What ends here is asking again: the note the generator was just given
                # named this exact rule, and it broke it anyway.
                stopped_because = _repeated_note(failures[-1])
                break
            critique = current.critique
            if critique is None or (critique.is_full and not current.rejected):
                break
            critique_notes = critique.notes
        return rounds, failures, stopped_because

    def _measure(
        self,
        draft: Draft,
        run: Run,
        lead: Lead,
        number: int,
        total: int,
        notes: tuple[str, ...],
    ) -> RevisionRound:
        """Write down one attempt, check it and measure it. Also for a refused one.

        A refused text is stored like any other: it is what the person will be shown if
        nothing better follows, and ``anlass review`` has to be able to find it.
        """
        if self.store is not None:
            self.store.save_draft(draft)
        verification = self._check_draft(draft, run, lead)
        return RevisionRound(
            number=number,
            total=total,
            draft=draft,
            verification=verification,
            critique=self._judge_draft(draft, run, lead, verification),
            notes=notes,
        )

    def _ask_drafter(
        self, lead: Lead, signal: Signal | None, notes: tuple[str, ...], takes_notes: bool
    ) -> Draft:
        assert self.drafter is not None
        if notes and takes_notes:
            return self.drafter.draft(
                lead, signal, list(self.facts), self.voice, revision_notes=list(notes)
            )
        return self.drafter.draft(lead, signal, list(self.facts), self.voice)

    def _check_draft(self, draft: Draft, run: Run, lead: Lead) -> VerificationResult | None:
        """Stage 7 on one attempt. Every attempt is checked, not only the last."""
        if self.verifier is None:
            return None
        result = self.verifier.verify(draft, list(self.facts), lead=lead, signal=run.signal)
        if self.store is not None:
            self.store.save_verification(result)
        return result

    def _judge_draft(
        self, draft: Draft, run: Run, lead: Lead, verification: VerificationResult | None
    ) -> Critique | None:
        if self.critic is None:
            return None
        return self.critic.critique(
            draft,
            lead=lead,
            signal=run.signal,
            facts=list(self.facts),
            verification=verification,
        )

    def _report_verification(self, run: Run, best: RevisionRound) -> bool:
        if self.verifier is None:
            run.steps.append(
                Step(
                    stage=Stage.VERIFY,
                    status=StepStatus.MISSING,
                    detail=(
                        "Keine Pruefstufe eingesetzt. Ein ungeprueftes Schreiben wird "
                        "nicht freigegeben."
                    ),
                )
            )
            return False
        result = best.verification
        assert result is not None
        if not result.passed:
            run.steps.append(
                Step(
                    stage=Stage.VERIFY,
                    status=StepStatus.STOPPED,
                    detail=(
                        f"{len(result.errors)} unbelegte Stellen, "
                        f"{len(result.warnings)} Hinweise. Der Entwurf geht zurueck."
                    ),
                    implementation=self.verifier.name,
                    produced=(result.id,),
                )
            )
            return False
        run.steps.append(
            Step(
                stage=Stage.VERIFY,
                status=StepStatus.OK,
                detail=f"Nichts Unbelegtes gefunden, {len(result.warnings)} Hinweise.",
                implementation=self.verifier.name,
                produced=(result.id,),
            )
        )
        return True

    def _report_critique(self, run: Run, best: RevisionRound) -> None:
        """Say what was measured. Never ends the run.

        A draft short of full marks is not rejected here - it went through as many
        revisions as it was allowed and now goes to the person **with its open points**,
        which is the whole point of naming them. The step therefore stays ``OK`` even
        when points are missing, and the detail carries what is open.
        """
        if self.critic is None:
            run.steps.append(
                Step(
                    stage=Stage.CRITIQUE,
                    status=StepStatus.MISSING,
                    detail=(
                        "Keine Guetemessung eingesetzt. Ob der Entwurf gut ist, misst "
                        "damit niemand - geprueft wird nur, ob er wahr ist."
                    ),
                )
            )
            return
        critique = best.critique
        assert critique is not None
        attempts = len(run.rounds)
        parts = [
            f"{critique.earned} von {critique.possible} Punkten nach {attempts} "
            f"{'Runde' if attempts == 1 else 'Runden'}."
        ]
        if best.rejected:
            # Said here as well as at the draft step, because this is the block a reader
            # scans for what is still open, and a broken hard rule is the most open
            # point there is.
            parts.append(f"Offen bleibt eine harte Vorgabe: {best.rejected_because}")
        if not critique.is_full:
            open_names = ", ".join(result.name for result in critique.open_criteria)
            if open_names:
                parts.append(f"Offen: {open_names}.")
            parts.append("Der beste Versuch geht mit seinen offenen Punkten an dich.")
        for entry in run.rounds:
            if entry.discarded:
                parts.append(f"Runde {entry.number} wurde verworfen: {entry.discarded_because}.")
        if self.max_revisions > 0 and not _accepts_revision_notes(self.drafter):
            parts.append(
                "Die Entwurfsstufe nimmt keine Ueberarbeitungsnotizen an, es wurde "
                "deshalb nicht ueberarbeitet."
            )
        parts.extend(critique.warnings)
        run.steps.append(
            Step(
                stage=Stage.CRITIQUE,
                status=StepStatus.OK,
                detail=" ".join(parts),
                implementation=self.critic.name,
            )
        )

    def _gate(self, run: Run) -> None:
        assert run.draft is not None
        if self.gate is None:
            run.steps.append(
                Step(
                    stage=Stage.GATE,
                    status=StepStatus.MISSING,
                    detail=(
                        "Keine Freigabestufe eingesetzt. Ohne Stufe 8 wird nichts "
                        "freigegeben und nichts versendet."
                    ),
                )
            )
            return
        objections = self.gate.check(
            run.draft, signal=run.signal, score=run.score, verification=run.verification
        )
        if objections:
            run.steps.append(
                Step(
                    stage=Stage.GATE,
                    status=StepStatus.STOPPED,
                    detail="Freigabe verweigert: " + "; ".join(objections),
                    implementation=self.gate.name,
                )
            )
            return
        run.steps.append(
            Step(
                stage=Stage.GATE,
                status=StepStatus.OK,
                detail="Nichts spricht gegen die Freigabe. Sie erfolgt erst auf Zuruf.",
                implementation=self.gate.name,
            )
        )

    # ---------------------------------------------------------------------- stage 8

    def approve(self, run: Run, *, reason: str = "") -> ApprovalRecord:
        """Record the release of the run's draft.

        Raises:
            anlass.errors.AnlassError: No gate is plugged in, there is no draft, or the
                gate objects. A release without stage 8 is not possible by design.
        """
        if run.draft is None:
            raise AnlassError("Es gibt keinen Entwurf, der freigegeben werden koennte.")
        if self.gate is None:
            raise AnlassError(
                "Es ist keine Freigabestufe eingesetzt (Stufe 8). Ohne sie wird nichts "
                "freigegeben - das ist keine Luecke, sondern die Regel."
            )
        objections = self.gate.check(
            run.draft, signal=run.signal, score=run.score, verification=run.verification
        )
        if objections:
            raise AnlassError("Freigabe nicht moeglich: " + "; ".join(objections))
        record = self.gate.approve(run.draft, actor=self.actor, reason=reason)
        run.approval = record
        if self.store is not None:
            self.store.save_approval(record)
        run.steps.append(
            Step(
                stage=Stage.GATE,
                status=StepStatus.OK,
                detail=f"Freigegeben durch {record.actor}.",
                implementation=self.gate.name,
                produced=(record.id,),
            )
        )
        return record

    # ---------------------------------------------------------------------- stage 9

    def deliver(self, run: Run, message: OutboundMessage) -> TransportReceipt:
        """Hand an approved draft to the transport. The only path out of this package.

        There is no argument that skips the approval check, and adding one would break
        rule 3. A caller that wants to send has to approve first, and approving is a
        recorded transition, not a flag.

        Raises:
            anlass.errors.AnlassError: No transport, no approval, or the preflight found
                a hard problem.
        """
        if self.transport is None:
            raise AnlassError("Es ist kein Versandweg eingesetzt (Stufe 9).")
        if run.draft is None:
            raise AnlassError("Es gibt keinen Entwurf, der versendet werden koennte.")
        if run.approval is None or run.approval.to_state is not ApprovalState.APPROVED:
            raise AnlassError(
                "Dieser Entwurf ist nicht freigegeben. Ohne Freigabe geht nichts raus, "
                "und es gibt keinen Schalter, der das abschaltet."
            )
        if message.draft_id != run.draft.id:
            raise AnlassError("Die Nachricht gehoert nicht zu diesem Entwurf.")
        problems = self.transport.preflight(message)
        if problems:
            run.steps.append(
                Step(
                    stage=Stage.SEND,
                    status=StepStatus.STOPPED,
                    detail="Vorpruefung: " + "; ".join(problems),
                    implementation=self.transport.name,
                )
            )
            raise AnlassError("Die Zustellbarkeits-Vorpruefung hat Einwaende: " + "; ".join(problems))
        receipt = self.transport.send(message)
        run.receipt = receipt
        if self.store is not None:
            # Two records, because they answer different questions. The transition says
            # the draft is out and is what the daily cap counts; the delivery keeps the
            # text that actually left and the reference a later reply is correlated
            # against - neither is derivable from the other.
            self.store.save_delivery(Delivery(message=message, receipt=receipt))
            self.store.save_approval(
                ApprovalRecord(
                    draft_id=run.draft.id,
                    from_state=ApprovalState.APPROVED,
                    to_state=ApprovalState.SENT,
                    actor=self.actor,
                    reason=receipt.reference,
                )
            )
        run.steps.append(
            Step(
                stage=Stage.SEND,
                status=StepStatus.OK if receipt.accepted else StepStatus.FAILED,
                detail=(
                    f"Uebergeben an {receipt.transport}: {receipt.reference or receipt.detail}"
                    if receipt.accepted
                    else f"Nicht angenommen: {receipt.detail}"
                ),
                implementation=receipt.transport,
                produced=(receipt.reference,) if receipt.reference else (),
            )
        )
        return receipt

    # --------------------------------------------------------------------- stage 10

    def poll_replies(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[Reply]:
        """Read new replies, classify them, write them down. Not part of a lead's run.

        What comes back is data and never an instruction: a reply that tells the reader
        to ignore their rules is a text to be classified and nothing more. Nothing in
        this method or below it reads a reply as anything but content.

        Raises:
            anlass.errors.AnlassError: No tracker is plugged in. Silently returning an
                empty list would read as "nobody answered", which is the one wrong
                answer a return channel can give.
        """
        if self.tracker is None:
            raise AnlassError(
                "Es ist kein Rueckkanal eingesetzt (Stufe 10). Ohne ihn ist 'keine "
                "Antwort' nicht von 'nicht nachgesehen' zu unterscheiden."
            )
        self._offer_sent_messages()
        replies = [
            reply if reply.kind is not ReplyKind.UNKNOWN else replace(reply, kind=self.tracker.classify(reply))
            for reply in self.tracker.poll(since=since, limit=limit)
        ]
        if self.store is not None:
            for reply in replies:
                self.store.save_reply(reply)
        return replies

    def _offer_sent_messages(self) -> None:
        """Hand the tracker what it needs to match a reply to a draft.

        Correlation data does not come out of a mailbox: it is the reference of what we
        sent, and that lives in the store. Offering it before every poll is what makes
        correlation survive a restart - the tracker's own memory is per process.

        ``register_sent`` is deliberately not part of the :class:`~anlass.interfaces.
        Tracker` protocol: a return channel that already knows the draft (a webhook, a
        CRM) needs nothing handed to it, so this is an offer to those that do.
        """
        register = getattr(self.tracker, "register_sent", None)
        if register is None or self.store is None:
            return
        for delivery in self.store.list_deliveries():
            if delivery.receipt.reference:
                register(
                    delivery.receipt.reference, delivery.draft_id, delivery.message.subject
                )

    def schedule_follow_ups(
        self, *, after: timedelta = DEFAULT_FOLLOW_UP_AFTER, now: datetime | None = None
    ) -> list[FollowUp]:
        """Put a due date on every delivered draft nobody really answered.

        Never sends, and there is no method here that would: a scheduled follow-up is a
        row with a date, and turning it into a message is a step a person takes. An
        automatic answer does not count as an answer - that is the same rule the response
        rate uses, and it has to be the same one, or the two numbers would disagree.

        Drafts that already have an entry are left alone, so polling twice does not move
        a due date that was set on the first poll.

        Raises:
            anlass.errors.AnlassError: No store. A follow-up nobody writes down is a
                reminder that dies with the process.
        """
        if self.store is None:
            raise AnlassError(
                "Ohne Speicher laesst sich kein Nachfassen terminieren - ein Termin, "
                "der den Programmlauf nicht ueberlebt, ist keiner."
            )
        already = {entry.draft_id for entry in self.store.list_follow_ups()}
        scheduled: list[FollowUp] = []
        for delivery in self.store.list_deliveries():
            draft_id = delivery.draft_id
            if draft_id in already or not delivery.receipt.accepted:
                continue
            if self._answered(draft_id):
                continue
            entry = FollowUp(
                draft_id=draft_id,
                due_at=delivery.receipt.sent_at + after,
                reason=(
                    f"Keine Antwort auf die Uebergabe vom "
                    f"{delivery.receipt.sent_at.date().isoformat()}."
                ),
            )
            self.store.save_follow_up(entry)
            already.add(draft_id)
            scheduled.append(entry)
        return scheduled

    def due_follow_ups(self, *, now: datetime | None = None) -> list[FollowUp]:
        """Scheduled follow-ups that are due and whose draft is still unanswered.

        The second half is why this is not just a store query: an answer that arrives
        after the entry was written makes it pointless, and showing it anyway would send
        somebody after a conversation that is already running.
        """
        if self.store is None:
            return []
        return [
            entry
            for entry in self.store.list_follow_ups(due_by=now or utcnow())
            if not self._answered(entry.draft_id)
        ]

    def _answered(self, draft_id: str) -> bool:
        """Whether a real person answered. A mailbox robot is not an answer."""
        if self.store is None:
            return False
        return any(
            reply.kind is not ReplyKind.AUTO_REPLY
            for reply in self.store.list_replies(draft_id=draft_id)
        )

    # ----------------------------------------------------------------------- helpers

    def describe(self) -> list[str]:
        """Which stage is filled by what, as German lines. Used by ``anlass init``."""
        filled: dict[Stage, str] = {
            Stage.SOURCE: self.source.name if self.source else "",
            Stage.ENRICH: ", ".join(p.name for p in self.enrichers),
            Stage.SCORE: self.scorer.name if self.scorer else "",
            Stage.SIGNAL: self.detector.name if self.detector else "",
            Stage.DRAFT: self.drafter.name if self.drafter else "",
            Stage.VERIFY: self.verifier.name if self.verifier else "",
            Stage.CRITIQUE: self.critic.name if self.critic else "",
            Stage.GATE: self.gate.name if self.gate else "",
            Stage.SEND: self.transport.name if self.transport else "",
            Stage.TRACK: self.tracker.name if self.tracker else "",
        }
        return [
            f"{stage.label:14} {name if name else 'noch nicht eingesetzt'}"
            for stage, name in filled.items()
        ]

    def _save_lead(self, lead: Lead) -> None:
        if self.store is not None:
            self.store.save_lead(lead)


def _rejection_note(reason: str) -> str:
    """Turn stage 6's refusal into an instruction, which is what a note has to be.

    The message says what was wrong ("Das Modell lieferte 6 Absaetze, erlaubt sind
    hoechstens 4."); a note has to say what to do. The wrapping sentence supplies that
    without paraphrasing the reason, so the concrete numbers reach the generator
    untouched.
    """
    return (
        f"Der vorige Entwurf wurde zurueckgewiesen und zaehlt nicht: {reason.rstrip('.')}. "
        "Schreibe ihn so, dass genau das nicht mehr zutrifft."
    )


def _repeated_note(reason: str) -> str:
    """Said in the run, because a loop that stops early has to say why it did."""
    return (
        "Abgebrochen: zwei Runden hintereinander scheiterten an derselben Verletzung "
        f"({reason.rstrip('.')}). Weiter zu bitten aendert daran nichts; der beste "
        "Versuch geht mit dem offenen Punkt an dich."
    )


#: Digits masked out of a failure message, so that "der Entwurf hat 3" and "der Entwurf
#: hat 4" are recognised as the same defect. Only the fallback - see
#: :func:`_violation_key`.
_DIGITS = re.compile(r"\d+")


def _violation_key(error: AnlassError) -> str:
    """What makes two failures "the same", for the purpose of stopping. "" if unknown.

    Only a round that **produced a text** can repeat a violation. A round that came back
    with nothing (unusable JSON, a call that timed out) failed, it did not break a rule:
    there is no instruction to give and nothing was ignored, so it stays bounded by the
    budget as before rather than ending the loop after two.

    For the rounds that did produce one, stage 6 names the broken rule itself
    (``DraftError.violation``), and that is the answer wherever it exists: it is the one
    thing that stays equal across rounds while the counts in the message change. For a
    generator that does not name it, the message with its numbers masked is the honest
    approximation - it recognises the same rule broken twice and not "some rejection
    twice", which would stop a loop that is making progress on two different defects.
    """
    if getattr(error, "draft", None) is None:
        return ""
    named = getattr(error, "violation", "")
    if named:
        return str(named)
    message = str(error).strip()
    return _DIGITS.sub("#", message) if message else ""


def _rank(entry: RevisionRound) -> tuple[int, int, int]:
    """How good an attempt is. Grounded first, then within the rules, points last.

    A truthful draft with fewer points beats a better-written one that invents
    something: the first can be sent after a correction, the second must not be sent at
    all. Below that the same argument once more: a draft that keeps the posting's
    countable rules beats one that breaks them, however well written, because breaking
    the first instruction of a posting loses the application before anyone reads it
    (Q1). Points decide among equals. Ties keep the earlier round,
    because :func:`max` returns the first maximum.
    """
    grounded = 0 if entry.verification is not None and not entry.verification.passed else 1
    compliant = 0 if entry.rejected else 1
    earned = entry.critique.earned if entry.critique is not None else 0
    return (grounded, compliant, earned)


def _accepts_revision_notes(drafter: Drafter | None) -> bool:
    """Whether this generator can be handed revision notes.

    The seam to stage 6 is one keyword argument, ``revision_notes``. Asking instead of
    assuming keeps a generator that does not have it usable: it runs one round, and the
    run says plainly that it was not revised, rather than producing the same draft four
    times or dying on a ``TypeError``.
    """
    if drafter is None:
        return False
    try:
        parameters = inspect.signature(drafter.draft).parameters
    except (TypeError, ValueError):  # builtins, C callables
        return False
    return "revision_notes" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
