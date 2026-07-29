"""Minimal DNS TXT lookup over UDP, standard library only.

No test in this package calls :func:`lookup_txt`: :class:`~anlass.transport.preflight.
DeliverabilityCheck` takes a resolver as a constructor argument, and every test injects
a fake one instead of touching the network. This is only the default that runs when
nobody does - written by hand because the project has no dependency on a DNS library
and this is a small enough protocol not to need one.

Answer compression is handled only for the common case (a pointer back into the
question, which is what real-world SPF/DMARC/DKIM answers use); a general-purpose
resolver this is not.
"""

from __future__ import annotations

import random
import socket
import struct

from ..errors import AnlassError

__all__ = ["DnsError", "lookup_txt"]

_TXT_TYPE = 16
_IN_CLASS = 1


class DnsError(AnlassError):
    """The query could not be sent or the answer could not be parsed.

    Not the same as "no such record", which is a normal, valid empty list.
    """


def _encode_name(domain: str) -> bytes:
    out = bytearray()
    for label in domain.strip(".").split("."):
        data = label.encode("ascii")
        if len(data) > 63:
            raise DnsError(f"DNS-Marke zu lang: '{label}'.")
        out.append(len(data))
        out.extend(data)
    out.append(0)
    return bytes(out)


def build_query(domain: str) -> tuple[bytes, int]:
    """The raw query bytes plus the transaction id used to match the answer."""
    query_id = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(domain) + struct.pack(">HH", _TXT_TYPE, _IN_CLASS)
    return header + question, query_id


def _skip_name(data: bytes, offset: int) -> int:
    while True:
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:  # compression pointer: two bytes, done
            return offset + 2
        offset += 1 + length


def parse_response(data: bytes, expected_id: int) -> list[str]:
    """TXT record contents of a raw DNS answer, or ``[]`` if there are none."""
    if len(data) < 12:
        raise DnsError("DNS-Antwort ist kuerzer als ein Kopfblock.")
    resp_id, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if resp_id != expected_id:
        raise DnsError("DNS-Antwort passt nicht zur Anfrage (falsche Transaktions-Kennung).")
    rcode = flags & 0x000F
    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset)
        offset += 4  # qtype + qclass
    if rcode != 0:
        return []  # NXDOMAIN and friends: no record, not an error
    records: list[str] = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            raise DnsError("DNS-Antwort ist beim Auswerten eines Eintrags abgeschnitten.")
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(data):
            raise DnsError("DNS-Antwort ist beim Auswerten eines Eintrags abgeschnitten.")
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype == _TXT_TYPE:
            pos = 0
            chunks = []
            while pos < len(rdata):
                length = rdata[pos]
                chunks.append(rdata[pos + 1 : pos + 1 + length].decode("ascii", "replace"))
                pos += 1 + length
            records.append("".join(chunks))
    return records


def lookup_txt(domain: str, *, nameserver: str = "1.1.1.1", timeout: float = 3.0) -> list[str]:
    """Every TXT record of ``domain``.

    Raises:
        DnsError: The query could not be sent or the answer could not be parsed.
    """
    query, query_id = build_query(domain)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(query, (nameserver, 53))
            data, _addr = sock.recvfrom(4096)
    except OSError as exc:
        raise DnsError(f"DNS-Abfrage fuer '{domain}' fehlgeschlagen: {exc}") from exc
    return parse_response(data, query_id)
