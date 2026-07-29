"""Deliverability preflight: check, do not warm up.

The plan (section 3, stage 9) is explicit about scope: check the enforced rules of the
large mailbox providers before a send, do not build the infrastructure that ramps a
fresh domain up over weeks - that is a product of its own. This module is the check
half only. It never sends anything and it never remembers a past send; everything it
needs about history (how many messages already went out today, measured complaint and
bounce rates, how old the sending domain is) is a value the caller supplies, because
this package has no metrics store of its own yet (see the result file for why).

Thresholds, as enforced by the large providers as of 2026 (plan section 3, stage 9):
complaint rate under 0.3%, bounces under 2%, 30-50 messages per mailbox per day as a
safe steady-state range, fresh domains starting at 5-10 per day and ramping over
4-6 weeks. The ramp *schedule* below is a coarse, documented guess at that curve, not a
measured one - there is no authoritative published ramp, every provider's guidance is a
rule of thumb. Treat ``ramp_schedule`` as overridable, not as ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Callable, Sequence

from ..models import OutboundMessage

__all__ = ["DEFAULT_DKIM_SELECTORS", "DEFAULT_RAMP_SCHEDULE", "DeliverabilityCheck"]

#: Selectors tried when looking for a DKIM record, most common first. A domain that
#: uses an uncommon selector will report a false "no DKIM found" - documented, not
#: silently hidden, because a hard-coded selector list is the only way to check DKIM
#: at all without reading the outgoing mail server's own configuration.
DEFAULT_DKIM_SELECTORS: tuple[str, ...] = ("default", "selector1", "selector2", "google", "k1", "mail")

_COMPLAINT_RATE_LIMIT = 0.003
_BOUNCE_RATE_LIMIT = 0.02

#: (max age in days, max messages per mailbox per day) while ramping a fresh domain,
#: ascending by age. A domain older than the last entry is not ramping any more and
#: only the steady-state ``daily_limit_per_mailbox`` applies.
DEFAULT_RAMP_SCHEDULE: tuple[tuple[int, int], ...] = (
    (7, 8),
    (14, 15),
    (21, 20),
    (28, 30),
    (42, 40),
)


def _domain_of(address: str) -> str:
    _name, email = parseaddr(address or "")
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


@dataclass
class DeliverabilityCheck:
    """Runs the checks a ``Transport`` calls from its own ``preflight``.

    Args:
        resolver: ``domain -> list of TXT record strings``. Defaults to a real DNS
            lookup; every test injects its own so no test ever queries real DNS.
        dkim_selectors: Selectors tried under ``<selector>._domainkey.<domain>``.
        daily_limit_per_mailbox: Steady-state ceiling once a domain is not fresh
            any more.
        ramp_schedule: See :data:`DEFAULT_RAMP_SCHEDULE`.
        require_unsubscribe: Whether a series send needs ``List-Unsubscribe``.
    """

    resolver: Callable[[str], list[str]] = field(default=None)  # type: ignore[assignment]
    dkim_selectors: Sequence[str] = DEFAULT_DKIM_SELECTORS
    daily_limit_per_mailbox: int = 40
    ramp_schedule: Sequence[tuple[int, int]] = DEFAULT_RAMP_SCHEDULE
    require_unsubscribe: bool = True

    def __post_init__(self) -> None:
        if self.resolver is None:
            from . import _dns

            self.resolver = _dns.lookup_txt

    def check(
        self,
        message: OutboundMessage,
        *,
        bulk: bool = True,
        sent_today: int = 0,
        domain_age_days: int | None = None,
        complaint_rate: float | None = None,
        bounce_rate: float | None = None,
    ) -> list[str]:
        """German problem descriptions. Empty means nothing speaks against sending.

        Args:
            bulk: Whether this is a series send (requires an unsubscribe option).
            sent_today: Messages already sent from this mailbox today.
            domain_age_days: Age of the sending domain, if known. ``None`` skips the
                ramp check rather than guessing.
            complaint_rate: Measured complaint rate (0.0-1.0), if the caller has one.
            bounce_rate: Measured bounce rate (0.0-1.0), if the caller has one.
        """
        problems: list[str] = []
        domain = _domain_of(message.sender or message.headers.get("From", ""))
        if not domain:
            problems.append(
                "Keine Absenderadresse angegeben, SPF/DKIM/DMARC koennen nicht geprueft werden."
            )
        else:
            problems.extend(self._dns_findings(domain))

        if bulk and self.require_unsubscribe and "List-Unsubscribe" not in message.headers:
            problems.append("Serienversand ohne Abmeldemoeglichkeit (List-Unsubscribe fehlt).")

        limit = self._limit_for_age(domain_age_days)
        if sent_today >= limit:
            reason = (
                f"Aufwaermphase (Domain {domain_age_days} Tage alt)"
                if domain_age_days is not None and limit < self.daily_limit_per_mailbox
                else "Tagesgrenze"
            )
            problems.append(f"{reason}: {sent_today} bereits versendet, Grenze liegt bei {limit}.")

        if complaint_rate is not None and complaint_rate >= _COMPLAINT_RATE_LIMIT:
            problems.append(
                f"Beschwerderate {complaint_rate:.2%} liegt bei oder ueber der Grenze von "
                f"{_COMPLAINT_RATE_LIMIT:.1%}."
            )
        if bounce_rate is not None and bounce_rate >= _BOUNCE_RATE_LIMIT:
            problems.append(
                f"Bounce-Rate {bounce_rate:.2%} liegt bei oder ueber der Grenze von {_BOUNCE_RATE_LIMIT:.0%}."
            )
        return problems

    def _limit_for_age(self, domain_age_days: int | None) -> int:
        if domain_age_days is None:
            return self.daily_limit_per_mailbox
        for max_age, max_per_day in self.ramp_schedule:
            if domain_age_days <= max_age:
                return max_per_day
        return self.daily_limit_per_mailbox

    def _dns_findings(self, domain: str) -> list[str]:
        spf = self._resolve(domain)
        if spf is None:
            return [f"DNS fuer '{domain}' nicht erreichbar, SPF/DKIM/DMARC konnten nicht geprueft werden."]

        problems: list[str] = []
        if not any(record.strip().lower().startswith("v=spf1") for record in spf):
            problems.append(f"Kein SPF-Eintrag fuer '{domain}' gefunden.")

        dmarc = self._resolve(f"_dmarc.{domain}") or []
        if not any(record.strip().lower().startswith("v=dmarc1") for record in dmarc):
            problems.append(f"Keine DMARC-Richtlinie fuer '{domain}' gefunden.")

        if not any(self._resolve(f"{selector}._domainkey.{domain}") for selector in self.dkim_selectors):
            selectors = ", ".join(self.dkim_selectors)
            problems.append(
                f"Kein DKIM-Eintrag fuer '{domain}' unter den ueblichen Selektoren gefunden "
                f"({selectors})."
            )
        return problems

    def _resolve(self, name: str) -> list[str] | None:
        try:
            return self.resolver(name)
        except Exception:
            return None
