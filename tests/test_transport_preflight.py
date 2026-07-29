from __future__ import annotations

from anlass.models import OutboundMessage
from anlass.transport.preflight import DeliverabilityCheck

CLEAN_RECORDS = {
    "beispiel.example": ["v=spf1 include:_spf.beispiel.example ~all"],
    "_dmarc.beispiel.example": ["v=DMARC1; p=reject; rua=mailto:dmarc@beispiel.example"],
    "default._domainkey.beispiel.example": ["v=DKIM1; k=rsa; p=abc"],
}


def _resolver(records: dict[str, list[str]]):
    return lambda name: records.get(name, [])


def _message(**overrides) -> OutboundMessage:
    fields = dict(
        draft_id="draft_test",
        recipient="roth@nordlicht.example",
        subject="Ihre Ausschreibung",
        body="Sehr geehrte Frau Roth, ...",
        sender="mara@beispiel.example",
        headers={"List-Unsubscribe": "<mailto:abmelden@beispiel.example>"},
    )
    fields.update(overrides)
    return OutboundMessage(**fields)


def test_clean_sender_produces_no_findings() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    assert check.check(_message()) == []


def test_missing_spf_is_reported() -> None:
    records = dict(CLEAN_RECORDS)
    records["beispiel.example"] = []
    check = DeliverabilityCheck(resolver=_resolver(records))

    problems = check.check(_message())
    assert any("SPF" in p for p in problems)


def test_missing_dmarc_is_reported() -> None:
    records = dict(CLEAN_RECORDS)
    records["_dmarc.beispiel.example"] = []
    check = DeliverabilityCheck(resolver=_resolver(records))

    problems = check.check(_message())
    assert any("DMARC" in p for p in problems)


def test_missing_dkim_under_every_selector_is_reported() -> None:
    records = dict(CLEAN_RECORDS)
    records["default._domainkey.beispiel.example"] = []
    check = DeliverabilityCheck(resolver=_resolver(records))

    problems = check.check(_message())
    assert any("DKIM" in p for p in problems)


def test_dkim_found_under_an_uncommon_selector_is_not_flagged() -> None:
    records = dict(CLEAN_RECORDS)
    del records["default._domainkey.beispiel.example"]
    records["google._domainkey.beispiel.example"] = ["v=DKIM1; k=rsa; p=xyz"]
    check = DeliverabilityCheck(resolver=_resolver(records))

    problems = check.check(_message())
    assert not any("DKIM" in p for p in problems)


def test_unreachable_dns_is_reported_once_not_per_record_type() -> None:
    def failing_resolver(name: str) -> list[str]:
        raise OSError("kein Netz")

    check = DeliverabilityCheck(resolver=failing_resolver)
    problems = check.check(_message())

    assert len(problems) == 1
    assert "nicht erreichbar" in problems[0]


def test_missing_sender_blocks_the_dns_checks_entirely() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(sender=None, headers={}))

    assert any("Absenderadresse" in p for p in problems)


def test_bulk_send_without_unsubscribe_is_reported() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(headers={}), bulk=True)

    assert any("Abmeldemoeglichkeit" in p for p in problems)


def test_single_send_does_not_need_an_unsubscribe_link() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(headers={}), bulk=False)

    assert not any("Abmeldemoeglichkeit" in p for p in problems)


def test_daily_limit_blocks_once_reached() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS), daily_limit_per_mailbox=40)

    assert check.check(_message(), sent_today=39) == []
    problems = check.check(_message(), sent_today=40)
    assert any("Tagesgrenze" in p or "Aufwaermphase" in p for p in problems)


def test_fresh_domain_ramp_is_stricter_than_the_steady_state_limit() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS), daily_limit_per_mailbox=40)

    problems = check.check(_message(), sent_today=10, domain_age_days=5)
    assert any("Aufwaermphase" in p for p in problems)

    # The same volume is fine once the domain is not fresh any more.
    assert check.check(_message(), sent_today=10, domain_age_days=None) == []


def test_complaint_rate_over_threshold_is_reported() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(), complaint_rate=0.01)

    assert any("Beschwerderate" in p for p in problems)


def test_bounce_rate_over_threshold_is_reported() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(), bounce_rate=0.05)

    assert any("Bounce" in p for p in problems)


def test_rates_below_threshold_are_not_reported() -> None:
    check = DeliverabilityCheck(resolver=_resolver(CLEAN_RECORDS))
    problems = check.check(_message(), complaint_rate=0.001, bounce_rate=0.005)

    assert problems == []
