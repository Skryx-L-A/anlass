"""Stage 3: call enrich providers in order, first one to deliver a field wins.

The point the plan calls out (PLAN Abschnitt 3): *which* provider filled a field is
recorded, always. That provenance is not this module's job to add - every
:class:`~anlass.interfaces.EnrichProvider` already stamps its own name onto the
:class:`anlass.models.FieldValue` it returns. This module's job is only to call
providers in the right order and stop asking about a field the moment it is
filled, and to never let a provider overwrite what an earlier one already found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..interfaces import EnrichProvider
from ..models import Lead

__all__ = ["Waterfall"]


@dataclass
class Waterfall:
    """Runs ``providers`` in order over ``fields`` of a lead.

    Args:
        providers: Tried in this order. A provider is skipped without being called
            at all if none of the fields it :attr:`~EnrichProvider.provides` are
            still missing - the point of declaring ``provides`` up front.
    """

    providers: Sequence[EnrichProvider]

    def enrich(self, lead: Lead, fields: Sequence[str]) -> Lead:
        """Fill as many of ``fields`` as possible. Mutates and returns ``lead``.

        Stops early once every field is filled. A provider that returns a field it
        was not asked for (outside the ``missing`` subset it was called with) is
        ignored for that field - the interface's own rule, enforced here as well as
        documented there.
        """
        for provider in self.providers:
            missing = lead.missing(fields)
            if not missing:
                break
            relevant = [name for name in missing if name in provider.provides]
            if not relevant:
                continue
            found = provider.enrich(lead, relevant)
            for name, value in found.items():
                if name not in relevant:
                    continue
                lead.set(name, value)
        return lead
