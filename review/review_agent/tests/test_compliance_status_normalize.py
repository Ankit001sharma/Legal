"""P1-5: ComplianceStatus typo normalization for LLM structured output."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from document_core.schemas.compliance import ComplianceStatus
from review_agent.schemas.compliance_llm import BatchComplianceItem, ComplianceLLMResult
from review_agent.schemas.compliance_status_utils import normalize_compliance_status
from review_agent.schemas.section_compare import (
    BatchFinalGapVerifyLLMResult,
    FinalGapVerifyItem,
    SectionCompareItem,
)


def test_typo_insufficient_polic_context_section_compare() -> None:
    item = SectionCompareItem(
        section_id="1",
        status="INSUFFICIENT_POLIC_CONTEXT",
        rationale="No policy text was available for this section.",
    )
    assert item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_typo_on_compliance_llm_result() -> None:
    item = ComplianceLLMResult(
        status="INSUFFICIENT_POLIC_CONTEXT",
        rationale="No matching playbook was retrieved for comparison.",
    )
    assert item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_typo_on_final_gap_item() -> None:
    item = FinalGapVerifyItem(
        section_id="2",
        status="INSUFFICIENT_POLIC_CONTEXT",
        rationale="Boilerplate section with no applicable playbook.",
    )
    assert item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_invalid_status_still_raises() -> None:
    with pytest.raises(ValidationError):
        SectionCompareItem(
            section_id="1",
            status="NOT_A_REAL_STATUS",
            rationale="This should fail validation.",
        )


def test_batch_gap_verify_with_typo_item() -> None:
    batch = BatchFinalGapVerifyLLMResult(
        items=[
            FinalGapVerifyItem(
                section_id="1",
                status=ComplianceStatus.INCONCLUSIVE,
                rationale="Contract liability cap appears low versus market norms.",
            ),
            FinalGapVerifyItem(
                section_id="2",
                status="INSUFFICIENT_POLIC_CONTEXT",
                rationale="Definitions section has no applicable playbook topic.",
            ),
        ]
    )
    assert len(batch.items) == 2
    assert batch.items[1].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_non_compliant_alias() -> None:
    assert normalize_compliance_status("NONCOMPLIANT") == ComplianceStatus.NON_COMPLIANT


def test_batch_compliance_item_typo() -> None:
    item = BatchComplianceItem(
        category_id="liability",
        status="INSUFFICIENT_POLICY_CONTEX",
        rationale="Insufficient policy context for liability dimension.",
    )
    assert item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
