"""Kennzahlen (stage 10, second half): response rate and the honest number.

Two halves on purpose: :mod:`anlass.metrics.collect` reads the store and assembles one
:class:`~anlass.metrics.report.DraftOutcome` per draft, :mod:`anlass.metrics.report`
aggregates and formats and touches no database.
"""

from __future__ import annotations

from .collect import collect_outcomes
from .report import DraftOutcome, RateStat, render_report, response_rate_by, score_band

__all__ = [
    "DraftOutcome",
    "RateStat",
    "collect_outcomes",
    "render_report",
    "response_rate_by",
    "score_band",
]
