"""Stage 4: score a lead against the criteria file, rules only, never a model.

A language model may extract fields (stage 3); it must not decide points here. Every
criterion is a small boolean expression (:mod:`anlass.score.expr`) evaluated against
the lead, so the total is reproducible and every rejection can name the exact
criterion or exclusion that caused it - the promise :class:`anlass.interfaces.Scorer`
makes.

The minimum score is enforced here as well as in the gate (:mod:`anlass.gate.limits`):
a lead below it is excluded at this stage, before a draft is ever written for it, and
the gate checks it again before sending in case a caller skipped scoring or the
criteria file changed between the two. Belt and suspenders on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..errors import ConfigError
from ..models import CriterionOutcome, Lead, ScoreResult
from .expr import CheckExpression

__all__ = ["RuleScorer"]


@dataclass(frozen=True, slots=True)
class _Criterion:
    name: str
    weight: int
    check: CheckExpression


class RuleScorer:
    """Implements :class:`anlass.interfaces.Scorer` over a criteria dict.

    Args:
        criteria: The raw dict :func:`anlass.profile.load_profile` reads from
            ``criteria.yaml`` - keys ``criteria``, ``exclusions``, ``limits`` and
            optionally ``context`` (extra names a check may reference besides the
            lead's own fields, e.g. ``home_locations`` for ``distance_km``).
        name: Stable identifier, written into every :class:`ScoreResult`.
    """

    def __init__(self, criteria: Mapping[str, Any], *, name: str = "rules") -> None:
        self._name = name
        self._context: dict[str, Any] = dict(criteria.get("context") or {})
        self._criteria = self._load_criteria(criteria.get("criteria") or [])
        self._exclusions = self._load_exclusions(criteria.get("exclusions") or [])
        limits = criteria.get("limits") or {}
        self._min_score = limits.get("min_score")

    @property
    def name(self) -> str:
        return self._name

    @staticmethod
    def _load_criteria(entries: Sequence[Any]) -> list[_Criterion]:
        criteria: list[_Criterion] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, Mapping):
                raise ConfigError(f"Kriterium {index} in der Kriteriendatei ist kein Objekt.")
            name = str(entry.get("name", "")).strip()
            if not name:
                raise ConfigError(f"Kriterium {index} in der Kriteriendatei hat keinen Namen.")
            if name in seen:
                raise ConfigError(f"Der Kriteriumsname '{name}' kommt mehrfach vor.")
            seen.add(name)
            try:
                weight = int(entry["weight"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"Kriterium '{name}' hat kein gueltiges Gewicht.") from exc
            check_source = str(entry.get("check", "")).strip()
            if not check_source:
                raise ConfigError(f"Kriterium '{name}' hat keine Pruefung.")
            criteria.append(_Criterion(name=name, weight=weight, check=CheckExpression(check_source)))
        if not criteria:
            raise ConfigError("Die Kriteriendatei enthaelt kein einziges Kriterium.")
        return criteria

    @staticmethod
    def _load_exclusions(entries: Sequence[Any]) -> list[CheckExpression]:
        return [CheckExpression(str(entry)) for entry in entries]

    def score(self, lead: Lead) -> ScoreResult:
        outcomes: list[CriterionOutcome] = []
        total = 0
        for criterion in self._criteria:
            passed = criterion.check.evaluate(lead, self._context)
            if passed:
                total += criterion.weight
            reason = (
                f"Kriterium '{criterion.name}' erfuellt (+{criterion.weight} Punkte): "
                f"{criterion.check.source}"
                if passed
                else f"Kriterium '{criterion.name}' nicht erfuellt (0 von {criterion.weight} "
                f"Punkten): {criterion.check.source}"
            )
            outcomes.append(
                CriterionOutcome(name=criterion.name, weight=criterion.weight, passed=passed, reason=reason)
            )

        context = dict(self._context)
        context["score"] = total
        excluded_by: str | None = None
        for exclusion in self._exclusions:
            if exclusion.evaluate(lead, context):
                excluded_by = exclusion.source
                break
        if excluded_by is None and self._min_score is not None and total < self._min_score:
            excluded_by = f"score < {self._min_score} (limits.min_score)"

        return ScoreResult(lead_id=lead.id, total=total, outcomes=tuple(outcomes), excluded_by=excluded_by)
