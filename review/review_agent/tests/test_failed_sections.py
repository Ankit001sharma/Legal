"""Tests for failed_sections helpers (Phase 29)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.resilience.failed_sections import (
    classify_degraded_entries,
    compare_failed_entries,
    failed_section_entry,
    retrieval_failed_entry,
)
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.schemas.section_compare import SectionCompareItem


def test_failed_section_entry_truncates_message():
    entry = failed_section_entry("s1", "compare", "compare_failed", "x" * 600)
    assert entry["section_id"] == "s1"
    assert len(entry["message"]) == 500


def test_compare_failed_entries_detects_insufficient_items():
    items = [
        SectionCompareItem(
            section_id="s1",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            rationale="Section compare failed: timeout",
        ),
        SectionCompareItem(
            section_id="s2",
            status=ComplianceStatus.COMPLIANT,
            rationale="ok section",
        ),
    ]
    failed = compare_failed_entries(items)
    assert len(failed) == 1
    assert failed[0]["stage"] == "compare"
    assert failed[0]["error_code"] == "compare_failed"


def test_classify_degraded_entries_llm_unavailable():
    classifications = {
        "s1": SectionCategoryResult(
            section_id="s1",
            categories=["general"],
            query_terms=[],
            substantive=True,
            classify_warning="llm_unavailable",
        ),
        "s2": SectionCategoryResult(
            section_id="s2",
            categories=["privacy"],
            query_terms=[],
            substantive=True,
            classify_warning="lexical_first=high",
        ),
    }
    failed = classify_degraded_entries(classifications)
    assert len(failed) == 1
    assert failed[0]["stage"] == "classify"


def test_retrieval_failed_entry_shape():
    entry = retrieval_failed_entry("s9", "connection reset")
    assert entry == {
        "section_id": "s9",
        "stage": "retrieve",
        "error_code": "retrieval_failed",
        "message": "connection reset",
    }
