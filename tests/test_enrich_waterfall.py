"""Waterfall: order determines precedence, no provider overwrites, none is asked
about a field it cannot fill, and a provider cannot smuggle in an unrequested field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from anlass.enrich.waterfall import Waterfall
from anlass.interfaces import EnrichProvider
from anlass.models import Field, FieldValue, Lead


@dataclass
class _StaticProvider:
    """A minimal EnrichProvider built only from the protocol - test double."""

    provider_name: str
    values: Mapping[str, object]
    calls: list[Sequence[str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def provides(self) -> frozenset[str]:
        return frozenset(self.values)

    def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
        self.calls.append(tuple(missing))
        return {
            name: FieldValue(value, provider=self.provider_name, confidence=0.7)
            for name, value in self.values.items()
            if name in missing
        }


def test_first_provider_to_deliver_a_field_wins():
    first = _StaticProvider("erster", {Field.ORGANIZATION: "Nordlicht"})
    second = _StaticProvider("zweiter", {Field.ORGANIZATION: "Anderer Name"})
    lead = Lead(source="test")
    Waterfall([first, second]).enrich(lead, [Field.ORGANIZATION])
    assert lead.value(Field.ORGANIZATION) == "Nordlicht"
    assert lead.provider_of(Field.ORGANIZATION) == "erster"
    assert second.calls == []  # nothing left missing once the first provider filled it


def test_falls_through_to_the_next_provider_for_a_different_field():
    first = _StaticProvider("erster", {Field.ORGANIZATION: "Nordlicht"})
    second = _StaticProvider("zweiter", {Field.LOCATION: "Berlin"})
    lead = Lead(source="test")
    Waterfall([first, second]).enrich(lead, [Field.ORGANIZATION, Field.LOCATION])
    assert lead.value(Field.ORGANIZATION) == "Nordlicht"
    assert lead.value(Field.LOCATION) == "Berlin"
    assert lead.provider_of(Field.LOCATION) == "zweiter"


def test_a_provider_is_never_called_for_a_field_it_does_not_provide():
    only_location = _StaticProvider("nur_ort", {Field.LOCATION: "Berlin"})
    lead = Lead(source="test")
    Waterfall([only_location]).enrich(lead, [Field.ORGANIZATION])
    assert only_location.calls == []


def test_a_provider_is_skipped_once_everything_is_already_filled():
    lead = Lead(source="test")
    lead.set(Field.ORGANIZATION, FieldValue("Nordlicht", provider="quelle"))
    late = _StaticProvider("spaet", {Field.ORGANIZATION: "sollte nie ankommen"})
    Waterfall([late]).enrich(lead, [Field.ORGANIZATION])
    assert late.calls == []
    assert lead.value(Field.ORGANIZATION) == "Nordlicht"


def test_an_already_filled_field_is_never_overwritten_even_if_a_provider_tries():
    class RogueProvider:
        @property
        def name(self) -> str:
            return "unartig"

        @property
        def provides(self) -> frozenset[str]:
            return frozenset({Field.ORGANIZATION, Field.LOCATION})

        def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
            return {
                Field.ORGANIZATION: FieldValue("ueberschrieben", provider="unartig"),
                Field.LOCATION: FieldValue("Berlin", provider="unartig"),
            }

    lead = Lead(source="test")
    lead.set(Field.ORGANIZATION, FieldValue("Nordlicht", provider="quelle"))
    Waterfall([RogueProvider()]).enrich(lead, [Field.ORGANIZATION, Field.LOCATION])
    assert lead.value(Field.ORGANIZATION) == "Nordlicht"
    assert lead.value(Field.LOCATION) == "Berlin"


def test_a_field_outside_missing_returned_by_a_provider_is_ignored():
    class OversharingProvider:
        @property
        def name(self) -> str:
            return "zuviel"

        @property
        def provides(self) -> frozenset[str]:
            return frozenset({Field.LOCATION, Field.HEADCOUNT})

        def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
            return {
                Field.LOCATION: FieldValue("Berlin", provider="zuviel"),
                Field.HEADCOUNT: FieldValue(50, provider="zuviel"),
            }

    lead = Lead(source="test")
    Waterfall([OversharingProvider()]).enrich(lead, [Field.LOCATION])
    assert lead.value(Field.LOCATION) == "Berlin"
    assert lead.value(Field.HEADCOUNT) is None


def test_returns_the_same_lead_instance_mutated():
    lead = Lead(source="test")
    result = Waterfall([]).enrich(lead, [Field.ORGANIZATION])
    assert result is lead


def test_conforms_to_the_enrich_provider_protocol():
    assert isinstance(_StaticProvider("x", {}), EnrichProvider)
