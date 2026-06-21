"""Tests for quote validation helpers."""

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.services.quote_validate import (
    anchor_quote_in_haystack,
    truncate_section,
    validate_and_normalize_quotes,
)


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


def test_anchor_paraphrased_quote_finds_verbatim_span():
    haystack = (
        "The Supplier's liability shall not exceed the fees paid "
        "in the preceding twelve (12) months of service."
    )
    candidate = (
        "liability shall not exceed the fees paid in the preceding "
        "twelve (12) months of service"
    )
    anchored = anchor_quote_in_haystack(candidate, haystack)
    assert anchored
    assert anchored in haystack


def test_validate_nc_kept_after_anchor():
    contract_text = (
        "The Supplier's liability shall not exceed the fees paid "
        "in the preceding three (3) months of service."
    )
    policy_text = "Liability cap must be no less than twelve (12) months of fees."
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote=(
            "liability shall not exceed the fees paid in the preceding "
            "three (3) months of service"
        ),
        policy_quote="Liability cap must be no less than twelve (12) months of fees.",
        rationale="Contract cap is below policy minimum.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text=contract_text,
        policy_text=policy_text,
    )
    assert normalized.status == ComplianceStatus.NON_COMPLIANT
    assert normalized.contract_quote in contract_text

