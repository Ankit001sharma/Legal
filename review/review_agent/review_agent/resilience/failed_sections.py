"""Helpers for degraded-section tracking (Phase 29)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_classify import SectionCategoryResult

_COMPARE_FAIL_PREFIX = "Section compare failed:"


def failed_section_entry(
    section_id: str,
    stage: str,
    error_code: str,
    message: str,
) -> dict[str, str]:
    return {
        "section_id": section_id,
        "stage": stage,
        "error_code": error_code,
        "message": message[:500],
    }


def compare_failed_entries(items: list[SectionCompareItem]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items:
        rationale = item.rationale or ""
        if item.status != ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT:
            continue
        if not rationale.startswith(_COMPARE_FAIL_PREFIX):
            continue
        out.append(
            failed_section_entry(
                item.section_id,
                "compare",
                "compare_failed",
                rationale.removeprefix(_COMPARE_FAIL_PREFIX).strip() or rationale,
            )
        )
    return out


def classify_degraded_entries(
    classifications: dict[str, SectionCategoryResult],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for section_id, result in classifications.items():
        warning = result.classify_warning or ""
        if "llm_unavailable" not in warning.lower():
            continue
        out.append(
            failed_section_entry(
                section_id,
                "classify",
                "llm_unavailable",
                warning,
            )
        )
    return out


def retrieval_failed_entry(section_id: str, message: str) -> dict[str, str]:
    return failed_section_entry(section_id, "retrieve", "retrieval_failed", message)


def zero_hit_failed_entry(section_id: str) -> dict[str, str]:
    return failed_section_entry(
        section_id,
        "retrieve",
        "retrieval_zero_hit",
        "No policy hits after retrieval attempts",
    )
