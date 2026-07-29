"""Whitebox tests for the hand-rolled DNS parser. No socket, no network.

These exercise :func:`anlass.transport._dns.parse_response` and
:func:`anlass.transport._dns.build_query` directly against hand-built byte strings -
never :func:`anlass.transport._dns.lookup_txt`, which is the only function in this
module that touches a socket and is not covered by this suite on purpose (see
``anlass/transport/preflight.py``: every check in this package takes an injectable
resolver instead of calling ``lookup_txt`` in a test).
"""

from __future__ import annotations

import struct

import pytest

from anlass.transport._dns import DnsError, build_query, parse_response

_ANSWER_TXT_TYPE = 16
_IN_CLASS = 1


def _txt_rdata(*chunks: str) -> bytes:
    out = bytearray()
    for chunk in chunks:
        data = chunk.encode("ascii")
        out.append(len(data))
        out.extend(data)
    return bytes(out)


def _fake_response(query_id: int, question: bytes, answers: list[bytes], rcode: int = 0) -> bytes:
    flags = 0x8180 | rcode
    header = struct.pack(">HHHHHH", query_id, flags, 1, len(answers), 0, 0)
    return header + question + b"".join(answers)


def _answer(rdata: bytes, rtype: int = _ANSWER_TXT_TYPE) -> bytes:
    # 0xC00C: a compression pointer back to the question name at offset 12.
    return b"\xc0\x0c" + struct.pack(">HHIH", rtype, _IN_CLASS, 300, len(rdata)) + rdata


def test_build_query_encodes_the_domain_as_labels() -> None:
    query, query_id = build_query("beispiel.example")

    assert 0 <= query_id <= 0xFFFF
    assert query[12:13] == b"\x08"  # length of the label "beispiel"
    assert query[13:21] == b"beispiel"
    assert query.endswith(struct.pack(">HH", 16, 1))  # QTYPE=TXT, QCLASS=IN


def test_parse_response_extracts_one_txt_record() -> None:
    query, query_id = build_query("beispiel.example")
    question = query[12:]
    response = _fake_response(query_id, question, [_answer(_txt_rdata("v=spf1 -all"))])

    assert parse_response(response, query_id) == ["v=spf1 -all"]


def test_parse_response_joins_multiple_txt_chunks() -> None:
    query, query_id = build_query("beispiel.example")
    question = query[12:]
    response = _fake_response(query_id, question, [_answer(_txt_rdata("v=spf1 ", "include:x ", "-all"))])

    assert parse_response(response, query_id) == ["v=spf1 include:x -all"]


def test_parse_response_returns_empty_list_for_nxdomain() -> None:
    query, query_id = build_query("keine-solche-domain.example")
    question = query[12:]
    response = _fake_response(query_id, question, [], rcode=3)

    assert parse_response(response, query_id) == []


def test_parse_response_rejects_a_mismatched_transaction_id() -> None:
    query, query_id = build_query("beispiel.example")
    question = query[12:]
    response = _fake_response(query_id, question, [_answer(_txt_rdata("v=spf1 -all"))])

    with pytest.raises(DnsError):
        parse_response(response, query_id ^ 0x1)


def test_parse_response_rejects_a_truncated_answer() -> None:
    query, query_id = build_query("beispiel.example")
    question = query[12:]
    response = _fake_response(query_id, question, [_answer(_txt_rdata("v=spf1 -all"))])

    with pytest.raises(DnsError):
        parse_response(response[:-2], query_id)
