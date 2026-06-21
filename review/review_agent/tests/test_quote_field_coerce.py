"""Tests for quote field coercion (Phase 22 P4)."""

from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.schemas.quote_field_utils import coerce_quote_field
from review_agent.schemas.section_compare import FinalGapVerifyItem, SectionCompareItem


def test_coerce_policy_quote_list():
    assert coerce_quote_field(["Vendor shall indemnify", "for all claims"]) == (
        "Vendor shall indemnify for all claims"
    )


def test_coerce_policy_quote_dict():
    assert coerce_quote_field({"text": "clause text here"}) == "clause text here"


def test_coerce_policy_quote_none():
    assert coerce_quote_field(None) == ""


def test_section_compare_item_accepts_list_policy_quote():
    item = SectionCompareItem(
        section_id="1",
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Policy cap not met in contract language.",
        policy_quote=["Vendor shall indemnify"],
    )
    assert item.policy_quote == "Vendor shall indemnify"


def test_compliance_llm_result_accepts_list_quotes():
    result = ComplianceLLMResult(
        status=ComplianceStatus.COMPLIANT,
        rationale="Contract meets policy requirement on liability cap.",
        contract_quote=["exact contract substring here"],
        policy_quote={"quote": "exact policy substring here"},
    )
    assert result.contract_quote == "exact contract substring here"
    assert result.policy_quote == "exact policy substring here"


def test_final_gap_verify_item_coerces_contract_quote():
    item = FinalGapVerifyItem(
        section_id="s1",
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Contract silent on mandatory HR requirement.",
        contract_quote=["some quote text"],
    )
    assert item.contract_quote == "some quote text"
