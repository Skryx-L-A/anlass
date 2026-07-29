"""Stage 8: :class:`anlass.interfaces.Gate`, wired to the anti-mass rule.

Composes the state machine (:mod:`anlass.gate.approve`) with the limits
(:mod:`anlass.gate.limits`): ``check`` is the dry run a caller uses before deciding to
approve, ``approve`` is the one place the transition is actually written down, and it
re-validates rather than trusting that ``check`` was called first.

**The gate looks everything up itself.** Score, verification and organisation come out
of the :class:`~anlass.interfaces.Store`, not out of the call. A caller may pass a
score or a verification it already has in hand - a run that has just scored a lead
should not force a round trip - but it cannot use that to raise a score the store
disagrees with: what is passed only fills in what the store does not know. Anything
else would turn the minimum-score rule into a promise the caller keeps for us.

Where the count lives matters as much as the rule: with a
:class:`~anlass.gate.limits.StoreSendLog` the daily cap is read from persisted
approvals and survives a restart. With the in-memory default it does not, which is why
that default is a test convenience and not what the wiring uses.
"""

from __future__ import annotations

from datetime import timedelta

from ..interfaces import Store
from ..models import ApprovalRecord, ApprovalState, Draft, Field, ScoreResult, Signal, VerificationResult, utcnow
from .approve import ApprovalError, transition
from .limits import SendLimits, SendLog, StoreSendLog, limit_violations

__all__ = ["RuleGate"]


class RuleGate:
    """Implements :class:`anlass.interfaces.Gate`.

    Args:
        store: Read for the draft's lead (organisation), its score, its latest
            verification, and written for the approval transition.
        limits: The anti-mass numbers from ``criteria.yaml``.
        send_log: Where release counts are read from. Defaults to
            :class:`~anlass.gate.limits.StoreSendLog` over the same store, so the daily
            cap survives a restart; pass an
            :class:`~anlass.gate.limits.InMemorySendLog` only where that does not
            matter.
        name: Stable identifier.
    """

    def __init__(
        self,
        store: Store,
        limits: SendLimits,
        *,
        send_log: SendLog | None = None,
        name: str = "rules",
    ) -> None:
        self._store = store
        self._limits = limits
        self._log = send_log or StoreSendLog(
            store, lookback_days=max(limits.days_between_same_organization, 1)
        )
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        draft: Draft,
        *,
        signal: Signal | None = None,
        score: ScoreResult | None = None,
        verification: VerificationResult | None = None,
    ) -> list[str]:
        """Reasons that speak against releasing this draft. Empty means: nothing does."""
        reasons: list[str] = []
        if signal is None and draft.signal_id is None:
            reasons.append("Kein Anlass vorhanden - Regel 1 kennt keine Ausnahme.")
        if verification is None:
            verification = self._store.latest_verification(draft.id)
        if verification is None:
            reasons.append("Keine Pruefung vorhanden.")
        elif not verification.passed:
            reasons.append(f"Pruefung nicht bestanden ({len(verification.errors)} Fehler).")
        score = self._score_for(draft, score)
        if score is not None and not score.accepted:
            reasons.append(f"Ausgeschlossen durch '{score.excluded_by}'.")
        reasons.extend(self._limit_reasons(draft, score))
        return reasons

    def approve(self, draft: Draft, *, actor: str, reason: str = "") -> ApprovalRecord:
        """Record the transition to APPROVED, or raise if it is not allowed.

        Re-runs :meth:`check` internally rather than trusting a prior call to it, so a
        caller that reaches this method directly gets the same protection as one that
        goes through :class:`anlass.pipeline.Pipeline`. It takes no score: the store
        holds one, and a release that depended on a number the caller supplies would be
        a bypass with extra steps.

        Raises:
            anlass.gate.approve.ApprovalError: An objection from ``check`` applies, or
                the underlying state transition is not allowed.
        """
        objections = self.check(draft)
        if objections:
            raise ApprovalError(f"Freigabe fuer Entwurf '{draft.id}' verweigert: " + "; ".join(objections))
        record = transition(self._store, draft.id, ApprovalState.APPROVED, actor=actor, reason=reason)
        self._log.record(self._organization_of(draft), record.decided_at)
        return record

    def _score_for(self, draft: Draft, passed_in: ScoreResult | None) -> ScoreResult | None:
        """The stored score, falling back to what the caller handed in.

        The store wins where it has an answer. A caller may only fill a gap, never
        replace a stored score with a friendlier one.
        """
        stored = self._store.latest_score(draft.lead_id)
        return stored if stored is not None else passed_in

    def _limit_reasons(self, draft: Draft, score: ScoreResult | None) -> list[str]:
        now = utcnow()
        organization = self._organization_of(draft)
        return limit_violations(
            self._limits,
            score=score.total if score is not None else None,
            organization=organization,
            sent_today=self._log.count_since(now - timedelta(days=1)),
            last_sent_to_organization=self._log.last_sent(organization) if organization else None,
            now=now,
        )

    def _organization_of(self, draft: Draft) -> str | None:
        lead = self._store.get_lead(draft.lead_id)
        return None if lead is None else lead.value(Field.ORGANIZATION)
