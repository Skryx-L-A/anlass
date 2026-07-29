"""SQLite store: no setup, one file, runs immediately.

Migrations are a numbered list of SQL scripts, applied in order, recorded in
``schema_version``. That is enough for a single-file store and keeps the dependency
count at zero. Adapters for Postgres and Supabase are phase 2 and implement the same
:class:`anlass.interfaces.Store` protocol.

Values of a lead field are stored as JSON so their type survives a round trip; every
field row carries its provenance columns, because a field without provenance is
useless to stage 7.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..errors import StoreError
from ..models import (
    ApprovalRecord,
    ApprovalState,
    CriterionOutcome,
    Delivery,
    Draft,
    FieldValue,
    Finding,
    FindingKind,
    FollowUp,
    Lead,
    OutboundMessage,
    Paragraph,
    Reply,
    ReplyKind,
    ScoreResult,
    Severity,
    Signal,
    TransportReceipt,
    VerificationResult,
    utcnow,
)

__all__ = ["SCHEMA_VERSION", "SqliteStore"]

MIGRATIONS: list[str] = [
    # 1 - initial schema
    """
    CREATE TABLE leads (
        id          TEXT PRIMARY KEY,
        source      TEXT NOT NULL,
        source_ref  TEXT,
        text        TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    );
    CREATE TABLE lead_fields (
        lead_id      TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        value_json   TEXT NOT NULL,
        provider     TEXT NOT NULL,
        confidence   REAL NOT NULL,
        retrieved_at TEXT NOT NULL,
        evidence     TEXT,
        PRIMARY KEY (lead_id, name)
    );
    CREATE TABLE signals (
        id           TEXT PRIMARY KEY,
        lead_id      TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        kind         TEXT NOT NULL,
        quote        TEXT NOT NULL,
        source_url   TEXT,
        start_offset INTEGER,
        end_offset   INTEGER,
        detector     TEXT NOT NULL DEFAULT '',
        detected_at  TEXT NOT NULL
    );
    CREATE INDEX idx_signals_lead ON signals(lead_id);
    CREATE TABLE drafts (
        id         TEXT PRIMARY KEY,
        lead_id    TEXT NOT NULL,
        signal_id  TEXT,
        subject    TEXT,
        model      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE draft_paragraphs (
        draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        text     TEXT NOT NULL,
        PRIMARY KEY (draft_id, position)
    );
    CREATE TABLE draft_paragraph_facts (
        draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        rank     INTEGER NOT NULL,
        fact_id  TEXT NOT NULL,
        PRIMARY KEY (draft_id, position, rank)
    );
    CREATE TABLE verifications (
        id         TEXT PRIMARY KEY,
        draft_id   TEXT NOT NULL,
        passed     INTEGER NOT NULL,
        checkers   TEXT NOT NULL,
        checked_at TEXT NOT NULL
    );
    CREATE INDEX idx_verifications_draft ON verifications(draft_id);
    CREATE TABLE verification_findings (
        verification_id TEXT NOT NULL REFERENCES verifications(id) ON DELETE CASCADE,
        position        INTEGER NOT NULL,
        kind            TEXT NOT NULL,
        severity        TEXT NOT NULL,
        message         TEXT NOT NULL,
        excerpt         TEXT NOT NULL DEFAULT '',
        paragraph       INTEGER,
        checker         TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (verification_id, position)
    );
    CREATE TABLE approvals (
        id         TEXT PRIMARY KEY,
        draft_id   TEXT NOT NULL,
        from_state TEXT NOT NULL,
        to_state   TEXT NOT NULL,
        actor      TEXT NOT NULL,
        reason     TEXT NOT NULL DEFAULT '',
        decided_at TEXT NOT NULL
    );
    CREATE INDEX idx_approvals_draft ON approvals(draft_id, decided_at);
    """,
    # 2 - scores (stage 4) so stage 8 can look one up instead of being handed one,
    #     and an index over all approvals by time so the daily cap survives a restart
    """
    CREATE TABLE scores (
        lead_id     TEXT PRIMARY KEY,
        total       INTEGER NOT NULL,
        excluded_by TEXT,
        scored_at   TEXT NOT NULL
    );
    CREATE TABLE score_outcomes (
        lead_id  TEXT NOT NULL REFERENCES scores(lead_id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        name     TEXT NOT NULL,
        weight   INTEGER NOT NULL,
        passed   INTEGER NOT NULL,
        reason   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (lead_id, position)
    );
    CREATE INDEX idx_approvals_time ON approvals(decided_at, to_state);
    """,
    # 3 - the return channel and what the metrics need: what actually left (with the
    #     text that left), what came back, what is scheduled - and the generated version
    #     of every draft, which is the yardstick for "corrected by hand".
    """
    CREATE TABLE draft_origins (
        draft_id TEXT PRIMARY KEY,
        text     TEXT NOT NULL
    );
    CREATE TABLE deliveries (
        id        TEXT PRIMARY KEY,
        draft_id  TEXT NOT NULL,
        recipient TEXT NOT NULL,
        subject   TEXT NOT NULL DEFAULT '',
        body      TEXT NOT NULL DEFAULT '',
        sender    TEXT,
        headers   TEXT NOT NULL DEFAULT '{}',
        transport TEXT NOT NULL,
        accepted  INTEGER NOT NULL,
        reference TEXT NOT NULL DEFAULT '',
        detail    TEXT NOT NULL DEFAULT '',
        sent_at   TEXT NOT NULL
    );
    CREATE INDEX idx_deliveries_draft ON deliveries(draft_id, sent_at);
    CREATE TABLE replies (
        message_id  TEXT PRIMARY KEY,
        id          TEXT NOT NULL,
        draft_id    TEXT,
        sender      TEXT NOT NULL DEFAULT '',
        subject     TEXT NOT NULL DEFAULT '',
        body        TEXT NOT NULL DEFAULT '',
        in_reply_to TEXT,
        kind        TEXT NOT NULL,
        received_at TEXT NOT NULL
    );
    CREATE INDEX idx_replies_draft ON replies(draft_id, received_at);
    CREATE TABLE follow_ups (
        draft_id   TEXT PRIMARY KEY,
        id         TEXT NOT NULL,
        due_at     TEXT NOT NULL,
        reason     TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_follow_ups_due ON follow_ups(due_at);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteStore:
    """Implements :class:`anlass.interfaces.Store` on a single SQLite file.

    Args:
        path: File path, or ``":memory:"`` for a throwaway store (tests use that).
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def migrate(self) -> None:
        cursor = self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self._connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        if current > SCHEMA_VERSION:
            raise StoreError(
                f"Die Datenbank hat Schemastand {current}, dieses Programm kennt nur "
                f"{SCHEMA_VERSION}. Bitte eine neuere Fassung von anlass verwenden."
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            self._connection.executescript(MIGRATIONS[version - 1])
            self._connection.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        self._connection.commit()
        cursor.close()

    # ----------------------------------------------------------------------- leads

    def save_lead(self, lead: Lead) -> None:
        with self._connection as connection:
            connection.execute(
                "INSERT INTO leads (id, source, source_ref, text, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET source=excluded.source, "
                "source_ref=excluded.source_ref, text=excluded.text",
                (lead.id, lead.source, lead.source_ref, lead.text, lead.created_at.isoformat()),
            )
            connection.execute("DELETE FROM lead_fields WHERE lead_id = ?", (lead.id,))
            connection.executemany(
                "INSERT INTO lead_fields "
                "(lead_id, name, value_json, provider, confidence, retrieved_at, evidence) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        lead.id,
                        name,
                        json.dumps(entry.value),
                        entry.provider,
                        entry.confidence,
                        entry.retrieved_at.isoformat(),
                        entry.evidence,
                    )
                    for name, entry in lead.fields.items()
                ],
            )

    def get_lead(self, lead_id: str) -> Lead | None:
        row = self._connection.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return None if row is None else self._read_lead(row)

    def list_leads(self, *, limit: int | None = None) -> list[Lead]:
        sql = "SELECT * FROM leads ORDER BY created_at DESC, id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._read_lead(row) for row in self._connection.execute(sql, params)]

    def lead_by_ref(self, source: str, source_ref: str) -> Lead | None:
        """Newest lead of this source carrying this reference, or ``None``."""
        if not source_ref:
            return None
        row = self._connection.execute(
            "SELECT * FROM leads WHERE source = ? AND source_ref = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (source, source_ref),
        ).fetchone()
        return None if row is None else self._read_lead(row)

    def _read_lead(self, row: sqlite3.Row) -> Lead:
        fields = {
            field_row["name"]: FieldValue(
                value=json.loads(field_row["value_json"]),
                provider=field_row["provider"],
                retrieved_at=_dt(field_row["retrieved_at"]),
                confidence=field_row["confidence"],
                evidence=field_row["evidence"],
            )
            for field_row in self._connection.execute(
                "SELECT * FROM lead_fields WHERE lead_id = ? ORDER BY name", (row["id"],)
            )
        }
        return Lead(
            source=row["source"],
            source_ref=row["source_ref"],
            text=row["text"],
            fields=fields,
            id=row["id"],
            created_at=_dt(row["created_at"]),
        )

    # --------------------------------------------------------------------- signals

    def save_signal(self, signal: Signal) -> None:
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO signals "
                "(id, lead_id, kind, quote, source_url, start_offset, end_offset, detector, detected_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    signal.id,
                    signal.lead_id,
                    signal.kind,
                    signal.quote,
                    signal.source_url,
                    signal.start,
                    signal.end,
                    signal.detector,
                    signal.detected_at.isoformat(),
                ),
            )

    def signals_for_lead(self, lead_id: str) -> list[Signal]:
        rows = self._connection.execute(
            "SELECT * FROM signals WHERE lead_id = ? ORDER BY detected_at, id", (lead_id,)
        )
        return [self._read_signal(row) for row in rows]

    def signal_by_id(self, signal_id: str) -> Signal | None:
        row = self._connection.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        return None if row is None else self._read_signal(row)

    @staticmethod
    def _read_signal(row: sqlite3.Row) -> Signal:
        return Signal(
            lead_id=row["lead_id"],
            kind=row["kind"],
            quote=row["quote"],
            source_url=row["source_url"],
            start=row["start_offset"],
            end=row["end_offset"],
            detector=row["detector"],
            id=row["id"],
            detected_at=_dt(row["detected_at"]),
        )

    # ---------------------------------------------------------------------- scores

    def save_score(self, result: ScoreResult) -> None:
        """One current score per lead. Re-scoring replaces, it does not pile up."""
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO scores (lead_id, total, excluded_by, scored_at) "
                "VALUES (?,?,?,?)",
                (result.lead_id, result.total, result.excluded_by, utcnow().isoformat()),
            )
            connection.execute("DELETE FROM score_outcomes WHERE lead_id = ?", (result.lead_id,))
            connection.executemany(
                "INSERT INTO score_outcomes (lead_id, position, name, weight, passed, reason) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (
                        result.lead_id,
                        position,
                        outcome.name,
                        outcome.weight,
                        1 if outcome.passed else 0,
                        outcome.reason,
                    )
                    for position, outcome in enumerate(result.outcomes)
                ],
            )

    def latest_score(self, lead_id: str) -> ScoreResult | None:
        row = self._connection.execute(
            "SELECT * FROM scores WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row is None:
            return None
        outcomes = tuple(
            CriterionOutcome(
                name=outcome_row["name"],
                weight=outcome_row["weight"],
                passed=bool(outcome_row["passed"]),
                reason=outcome_row["reason"],
            )
            for outcome_row in self._connection.execute(
                "SELECT * FROM score_outcomes WHERE lead_id = ? ORDER BY position", (lead_id,)
            )
        )
        return ScoreResult(
            lead_id=row["lead_id"],
            total=row["total"],
            outcomes=outcomes,
            excluded_by=row["excluded_by"],
        )

    # ---------------------------------------------------------------------- drafts

    def save_draft(self, draft: Draft) -> None:
        """Store the draft, and on its first save the text as it was generated.

        ``INSERT OR IGNORE`` on ``draft_origins`` is what makes the honest metric
        possible: the first version wins and every later save leaves it alone, so a
        corrected draft can still be held against what the drafter wrote.
        """
        with self._connection as connection:
            connection.execute(
                "INSERT OR IGNORE INTO draft_origins (draft_id, text) VALUES (?,?)",
                (draft.id, draft.text),
            )
            connection.execute(
                "INSERT OR REPLACE INTO drafts (id, lead_id, signal_id, subject, model, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    draft.id,
                    draft.lead_id,
                    draft.signal_id,
                    draft.subject,
                    draft.model,
                    draft.created_at.isoformat(),
                ),
            )
            connection.execute("DELETE FROM draft_paragraphs WHERE draft_id = ?", (draft.id,))
            connection.execute("DELETE FROM draft_paragraph_facts WHERE draft_id = ?", (draft.id,))
            connection.executemany(
                "INSERT INTO draft_paragraphs (draft_id, position, text) VALUES (?,?,?)",
                [(draft.id, i, p.text) for i, p in enumerate(draft.paragraphs)],
            )
            connection.executemany(
                "INSERT INTO draft_paragraph_facts (draft_id, position, rank, fact_id) VALUES (?,?,?,?)",
                [
                    (draft.id, i, rank, fact_id)
                    for i, p in enumerate(draft.paragraphs)
                    for rank, fact_id in enumerate(p.fact_ids)
                ],
            )

    def get_draft(self, draft_id: str) -> Draft | None:
        row = self._connection.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        citations: dict[int, list[str]] = {}
        for fact_row in self._connection.execute(
            "SELECT position, fact_id FROM draft_paragraph_facts WHERE draft_id = ? ORDER BY position, rank",
            (draft_id,),
        ):
            citations.setdefault(fact_row["position"], []).append(fact_row["fact_id"])
        paragraphs = [
            Paragraph(text=p_row["text"], fact_ids=tuple(citations.get(p_row["position"], ())))
            for p_row in self._connection.execute(
                "SELECT position, text FROM draft_paragraphs WHERE draft_id = ? ORDER BY position",
                (draft_id,),
            )
        ]
        return Draft(
            lead_id=row["lead_id"],
            paragraphs=paragraphs,
            signal_id=row["signal_id"],
            subject=row["subject"],
            model=row["model"],
            id=row["id"],
            created_at=_dt(row["created_at"]),
        )

    def list_drafts(self, *, limit: int | None = None) -> list[Draft]:
        sql = "SELECT id FROM drafts ORDER BY created_at DESC, id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        ids = [row["id"] for row in self._connection.execute(sql, params)]
        drafts = [self.get_draft(draft_id) for draft_id in ids]
        return [draft for draft in drafts if draft is not None]

    def generated_text(self, draft_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT text FROM draft_origins WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return None if row is None else str(row["text"])

    # --------------------------------------------------------------- verifications

    def save_verification(self, result: VerificationResult) -> None:
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO verifications (id, draft_id, passed, checkers, checked_at) "
                "VALUES (?,?,?,?,?)",
                (
                    result.id,
                    result.draft_id,
                    1 if result.passed else 0,
                    json.dumps(list(result.checkers)),
                    result.checked_at.isoformat(),
                ),
            )
            connection.execute(
                "DELETE FROM verification_findings WHERE verification_id = ?", (result.id,)
            )
            connection.executemany(
                "INSERT INTO verification_findings "
                "(verification_id, position, kind, severity, message, excerpt, paragraph, checker) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        result.id,
                        position,
                        finding.kind.value,
                        finding.severity.value,
                        finding.message,
                        finding.excerpt,
                        finding.paragraph,
                        finding.checker,
                    )
                    for position, finding in enumerate(result.findings)
                ],
            )

    def latest_verification(self, draft_id: str) -> VerificationResult | None:
        row = self._connection.execute(
            "SELECT * FROM verifications WHERE draft_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        findings = tuple(
            Finding(
                kind=FindingKind(f_row["kind"]),
                severity=Severity(f_row["severity"]),
                message=f_row["message"],
                excerpt=f_row["excerpt"],
                paragraph=f_row["paragraph"],
                checker=f_row["checker"],
            )
            for f_row in self._connection.execute(
                "SELECT * FROM verification_findings WHERE verification_id = ? ORDER BY position",
                (row["id"],),
            )
        )
        return VerificationResult(
            draft_id=row["draft_id"],
            findings=findings,
            checkers=tuple(json.loads(row["checkers"])),
            id=row["id"],
            checked_at=_dt(row["checked_at"]),
        )

    # -------------------------------------------------------------------- approval

    def save_approval(self, record: ApprovalRecord) -> None:
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO approvals "
                "(id, draft_id, from_state, to_state, actor, reason, decided_at) VALUES (?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.draft_id,
                    record.from_state.value,
                    record.to_state.value,
                    record.actor,
                    record.reason,
                    record.decided_at.isoformat(),
                ),
            )

    def approvals_for_draft(self, draft_id: str) -> list[ApprovalRecord]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE draft_id = ? ORDER BY decided_at, id", (draft_id,)
        )
        return [self._read_approval(row) for row in rows]

    @staticmethod
    def _read_approval(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            draft_id=row["draft_id"],
            from_state=ApprovalState(row["from_state"]),
            to_state=ApprovalState(row["to_state"]),
            actor=row["actor"],
            reason=row["reason"],
            id=row["id"],
            decided_at=_dt(row["decided_at"]),
        )

    def approvals_since(
        self, start: datetime, *, to_state: ApprovalState | None = None
    ) -> list[ApprovalRecord]:
        """Transitions at or after ``start``, across all drafts, newest first.

        Timestamps are compared as ISO strings, which is only correct because every
        timestamp in this package is timezone-aware UTC (:func:`anlass.models.utcnow`)
        and therefore has the same shape and offset - lexical order equals chronological
        order. A naive or differently offset value would sort wrong, so it is rejected
        here rather than silently mis-counted.
        """
        if start.tzinfo is None:
            raise StoreError(
                "approvals_since braucht einen Zeitpunkt mit Zeitzone; alle Zeitstempel "
                "in anlass sind UTC (siehe anlass.models.utcnow)."
            )
        sql = "SELECT * FROM approvals WHERE decided_at >= ?"
        params: tuple[Any, ...] = (start.astimezone(timezone.utc).isoformat(),)
        if to_state is not None:
            sql += " AND to_state = ?"
            params += (to_state.value,)
        sql += " ORDER BY decided_at DESC, id DESC"
        return [self._read_approval(row) for row in self._connection.execute(sql, params)]

    def current_state(self, draft_id: str) -> ApprovalState:
        history: Sequence[ApprovalRecord] = self.approvals_for_draft(draft_id)
        return history[-1].to_state if history else ApprovalState.PENDING

    # ------------------------------------------------------------------ deliveries

    def save_delivery(self, delivery: Delivery) -> None:
        message, receipt = delivery.message, delivery.receipt
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO deliveries "
                "(id, draft_id, recipient, subject, body, sender, headers, transport, "
                " accepted, reference, detail, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    delivery.id,
                    message.draft_id,
                    message.recipient,
                    message.subject,
                    message.body,
                    message.sender,
                    json.dumps(dict(message.headers)),
                    receipt.transport,
                    1 if receipt.accepted else 0,
                    receipt.reference,
                    receipt.detail,
                    receipt.sent_at.isoformat(),
                ),
            )

    def list_deliveries(
        self, *, draft_id: str | None = None, limit: int | None = None
    ) -> list[Delivery]:
        sql = "SELECT * FROM deliveries"
        params: tuple[Any, ...] = ()
        if draft_id is not None:
            sql += " WHERE draft_id = ?"
            params += (draft_id,)
        sql += " ORDER BY sent_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        return [
            Delivery(
                message=OutboundMessage(
                    draft_id=row["draft_id"],
                    recipient=row["recipient"],
                    subject=row["subject"],
                    body=row["body"],
                    sender=row["sender"],
                    headers=json.loads(row["headers"]),
                ),
                receipt=TransportReceipt(
                    transport=row["transport"],
                    accepted=bool(row["accepted"]),
                    reference=row["reference"],
                    detail=row["detail"],
                    sent_at=_dt(row["sent_at"]),
                ),
                id=row["id"],
            )
            for row in self._connection.execute(sql, params)
        ]

    # --------------------------------------------------------------------- replies

    def save_reply(self, reply: Reply) -> None:
        """Keyed by the message id: the same mail polled twice stays one reply."""
        with self._connection as connection:
            connection.execute(
                "INSERT INTO replies "
                "(message_id, id, draft_id, sender, subject, body, in_reply_to, kind, received_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET draft_id=excluded.draft_id, "
                "kind=excluded.kind, subject=excluded.subject, body=excluded.body",
                (
                    reply.message_id,
                    reply.id,
                    reply.draft_id,
                    reply.sender,
                    reply.subject,
                    reply.body,
                    reply.in_reply_to,
                    reply.kind.value,
                    reply.received_at.isoformat(),
                ),
            )

    def list_replies(
        self, *, draft_id: str | None = None, limit: int | None = None
    ) -> list[Reply]:
        sql = "SELECT * FROM replies"
        params: tuple[Any, ...] = ()
        if draft_id is not None:
            sql += " WHERE draft_id = ?"
            params += (draft_id,)
        sql += " ORDER BY received_at DESC, message_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        return [
            Reply(
                message_id=row["message_id"],
                sender=row["sender"],
                subject=row["subject"],
                body=row["body"],
                in_reply_to=row["in_reply_to"],
                draft_id=row["draft_id"],
                kind=ReplyKind(row["kind"]),
                id=row["id"],
                received_at=_dt(row["received_at"]),
            )
            for row in self._connection.execute(sql, params)
        ]

    # ------------------------------------------------------------------ follow-ups

    def save_follow_up(self, entry: FollowUp) -> None:
        with self._connection as connection:
            connection.execute(
                "INSERT OR REPLACE INTO follow_ups (draft_id, id, due_at, reason, created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    entry.draft_id,
                    entry.id,
                    entry.due_at.isoformat(),
                    entry.reason,
                    entry.created_at.isoformat(),
                ),
            )

    def list_follow_ups(
        self, *, draft_id: str | None = None, due_by: datetime | None = None
    ) -> list[FollowUp]:
        """Earliest due date first. ``due_by`` is compared the way ``approvals_since`` is.

        Same reasoning as there: ISO strings sort chronologically only because every
        timestamp in this package is timezone-aware UTC, so a naive one is refused
        instead of being mis-compared.
        """
        if due_by is not None and due_by.tzinfo is None:
            raise StoreError(
                "list_follow_ups braucht einen Zeitpunkt mit Zeitzone; alle Zeitstempel "
                "in anlass sind UTC (siehe anlass.models.utcnow)."
            )
        sql = "SELECT * FROM follow_ups"
        clauses: list[str] = []
        params: tuple[Any, ...] = ()
        if draft_id is not None:
            clauses.append("draft_id = ?")
            params += (draft_id,)
        if due_by is not None:
            clauses.append("due_at <= ?")
            params += (due_by.astimezone(timezone.utc).isoformat(),)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY due_at, draft_id"
        return [
            FollowUp(
                draft_id=row["draft_id"],
                due_at=_dt(row["due_at"]),
                reason=row["reason"],
                id=row["id"],
                created_at=_dt(row["created_at"]),
            )
            for row in self._connection.execute(sql, params)
        ]
