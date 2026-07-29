"""The contract is usable without asking back.

The point of this module is the acceptance criterion for phase 1: somebody must be
able to build a ``Source`` or a ``Transport`` against ``interfaces.py`` alone. So both
are built here, from the docstrings only, and checked against the protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator, Mapping, Sequence

from anlass.draft.generate import FactGroundedDrafter
from anlass.draft.verify import GroundingVerifier
from anlass.interfaces import (
    Drafter,
    EnrichProvider,
    LLM,
    Source,
    Store,
    Transport,
    Verifier,
)
from anlass.llm.fake import FakeLLM
from anlass.models import (
    FieldValue,
    Lead,
    OutboundMessage,
    RawRecord,
    TransportReceipt,
)
from anlass.store.sqlite import SqliteStore


class FileSource:
    """A source built only from the protocol docstring: reads records from memory."""

    def __init__(self, records: Sequence[RawRecord]) -> None:
        self._records = list(records)

    @property
    def name(self) -> str:
        return "datei"

    def fetch(self, *, since: datetime | None = None, limit: int | None = None) -> Iterator[RawRecord]:
        count = 0
        for record in self._records:
            if since is not None and record.fetched_at <= since:
                continue
            yield record
            count += 1
            if limit is not None and count >= limit:
                return

    def normalize(self, record: RawRecord) -> Lead:
        lead = Lead(source=self.name, source_ref=record.external_id, text=record.text)
        organization = record.data.get("organization")
        if organization:
            lead.set(
                "organization",
                FieldValue(organization, provider=self.name, confidence=1.0, evidence=record.text[:60]),
            )
        return lead


class MemoryTransport:
    """A transport built only from the protocol docstring: keeps what it 'sent'."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    @property
    def name(self) -> str:
        return "speicher"

    def preflight(self, message: OutboundMessage) -> list[str]:
        problems: list[str] = []
        if "@" not in message.recipient:
            problems.append("Die Empfaengeradresse enthaelt kein at-Zeichen.")
        if not message.subject.strip():
            problems.append("Die Betreffzeile ist leer.")
        return problems

    def send(self, message: OutboundMessage) -> TransportReceipt:
        self.sent.append(message)
        return TransportReceipt(transport=self.name, accepted=True, reference=str(len(self.sent)))


class StaticEnricher:
    """Fills a single field and never overwrites - the waterfall does the writing."""

    @property
    def name(self) -> str:
        return "statisch"

    @property
    def provides(self) -> frozenset[str]:
        return frozenset({"contact_email"})

    def enrich(self, lead: Lead, missing: Sequence[str]) -> Mapping[str, FieldValue]:
        if "contact_email" not in missing:
            return {}
        return {"contact_email": FieldValue("kontakt@beispiel.example", provider=self.name, confidence=0.6)}


def test_a_source_written_against_the_protocol_conforms():
    source = FileSource(
        [RawRecord(source="datei", external_id="1", text="Wir bauen selbst.", data={"organization": "Nordlicht"})]
    )
    assert isinstance(source, Source)
    records = list(source.fetch())
    lead = source.normalize(records[0])
    assert lead.value("organization") == "Nordlicht"
    assert lead.provider_of("organization") == "datei"


def test_fetch_honours_limit():
    records = [RawRecord(source="datei", external_id=str(i), text="x") for i in range(5)]
    assert len(list(FileSource(records).fetch(limit=2))) == 2


def test_a_transport_written_against_the_protocol_conforms():
    transport = MemoryTransport()
    assert isinstance(transport, Transport)
    message = OutboundMessage(draft_id="d", recipient="kein-at-zeichen", subject="", body="x")
    assert len(transport.preflight(message)) == 2
    assert transport.sent == []
    good = OutboundMessage(draft_id="d", recipient="a@b.example", subject="Hallo", body="x")
    assert transport.preflight(good) == []
    assert transport.send(good).accepted is True


def test_enrich_provider_conforms_and_reports_only_what_it_found():
    provider = StaticEnricher()
    assert isinstance(provider, EnrichProvider)
    lead = Lead(source="datei")
    assert provider.enrich(lead, ["organization"]) == {}
    assert "contact_email" in provider.enrich(lead, ["contact_email"])


def test_shipped_implementations_conform():
    assert isinstance(FakeLLM(), LLM)
    assert isinstance(SqliteStore(), Store)
    assert isinstance(GroundingVerifier(), Verifier)
    assert isinstance(FactGroundedDrafter(llm=FakeLLM()), Drafter)
