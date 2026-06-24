"""Tests for quote validation helpers."""

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.services.quote_validate import truncate_section, validate_and_normalize_quotes


def test_truncate_section_adds_marker():
    text = "word " * 5000
    out = truncate_section(text, max_chars=100)
    assert "truncated" in out


def test_invalid_quotes_downgraded():
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="not in text",
        policy_quote="also missing",
        rationale="Mismatch on liability cap requirements in the agreement.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text="Contract limits liability to fees paid.",
        policy_text="Policy limits liability to twelve months fees.",
    )
    assert normalized.status == ComplianceStatus.INCONCLUSIVE
