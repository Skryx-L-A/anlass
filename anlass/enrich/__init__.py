"""Stage 3: fill missing lead fields, one provider at a time, until one delivers.

:class:`~anlass.enrich.waterfall.Waterfall` is the orchestrator; :mod:`.providers`
holds what it calls: a page fetch, an organisation-website guess, and a
model-backed extraction from prose. Every value written here carries the provider
that found it - see :attr:`anlass.models.FieldValue.provider` - because without
that provenance stage 7 cannot tell a fact from a guess (PLAN Abschnitt 3).
"""

from __future__ import annotations

from .waterfall import Waterfall

__all__ = ["Waterfall"]
