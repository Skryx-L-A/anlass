"""Core types: the invariants that the later stages rely on."""

from __future__ import annotations

import pytest

from anlass.models import (
    ApprovalRecord,
    ApprovalState,
    Draft,
    Fact,
    FieldValue,
    Finding,
    FindingKind,
    Lead,
    Paragraph,
    ScoreResult,
    CriterionOutcome,
    Severity,
    Signal,
    VerificationResult,
    facts_by_id,
    utcnow,
)


def test_field_value_demands_provenance():
    with pytest.raises(ValueError):
        FieldValue("Nordlicht Systeme", provider="")


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_field_value_rejects_impossible_confidence(confidence):
    with pytest.raises(ValueError):
        FieldValue("x", provider="datei", confidence=confidence)


def test_timestamps_are_timezone_aware():
    assert FieldValue("x", provider="datei").retrieved_at.tzinfo is not None
    assert utcnow().tzinfo is not None


def test_waterfall_keeps_the_first_provider():
    lead = Lead(source="datei")
    assert lead.set("role", FieldValue("Werkstudent", provider="datei")) is True
    assert lead.set("role", FieldValue("Praktikant", provider="extraktion")) is False
    assert lead.value("role") == "Werkstudent"
    assert lead.provider_of("role") == "datei"
    assert lead.set("role", FieldValue("Praktikant", provider="mensch"), overwrite=True) is True
    assert lead.provider_of("role") == "mensch"


def test_missing_reports_unfilled_fields():
    lead = Lead(source="datei")
    lead.set("role", FieldValue("Werkstudent", provider="datei"))
    assert lead.missing(["role", "organization"]) == ["organization"]


def test_signal_without_quote_is_not_a_signal():
    with pytest.raises(ValueError):
        Signal(lead_id="lead_1", kind="eigenbau", quote="   ")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": " ", "claim": "a", "source": "b"},
        {"id": "a", "claim": " ", "source": "b"},
        {"id": "a", "claim": "b", "source": " "},
    ],
)
def test_fact_demands_id_claim_and_source(kwargs):
    with pytest.raises(ValueError):
        Fact(**kwargs)


def test_facts_by_id_rejects_duplicates():
    with pytest.raises(ValueError):
        facts_by_id([Fact("a", "x", "y"), Fact("a", "z", "y")])


def test_draft_text_and_citations():
    draft = Draft(
        lead_id="lead_1",
        paragraphs=[
            Paragraph("Erster Absatz.", ["f1", "f2"]),
            Paragraph("Zweiter Absatz.", ["f2"]),
            Paragraph("Gruss.", []),
        ],
    )
    assert draft.text == "Erster Absatz.\n\nZweiter Absatz.\n\nGruss."
    assert draft.fact_ids == ("f1", "f2")
    assert draft.paragraphs[0].fact_ids == ("f1", "f2")


def test_verification_passes_only_without_errors():
    warning = Finding(FindingKind.UNCITED_SUPPORT, Severity.WARNING, "Hinweis")
    error = Finding(FindingKind.UNSUPPORTED_NUMBER, Severity.ERROR, "Zahl ohne Beleg")
    assert VerificationResult("d", findings=(warning,)).passed is True
    result = VerificationResult("d", findings=(warning, error))
    assert result.passed is False
    assert result.errors == (error,)
    assert result.warnings == (warning,)


def test_approval_is_a_transition_with_a_protocol():
    record = ApprovalRecord(
        draft_id="d",
        from_state=ApprovalState.PENDING,
        to_state=ApprovalState.APPROVED,
        actor="mensch",
        reason="geprueft",
    )
    assert record.decided_at.tzinfo is not None
    assert record.to_state is ApprovalState.APPROVED


def test_score_result_reports_the_criterion_that_excluded():
    result = ScoreResult(
        lead_id="lead_1",
        total=4,
        outcomes=(CriterionOutcome("small_organization", 2, True, "headcount 40 < 200"),),
        excluded_by="score < 6",
    )
    assert result.accepted is False
    assert result.outcomes[0].reason
