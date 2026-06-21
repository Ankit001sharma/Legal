"""Guarantee every reviewable contract section has a report finding."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, Severity

from review_agent.config import ReviewSettings, get_settings
from review_agent.services.section_filter import filter_review_sections
from review_agent.services.section_gap_status import resolve_gap_finding_status


@dataclass
class SectionCoverageResult:
    findings: list[ComplianceFinding]
    warnings: list[str] = field(default_factory=list)
    uncovered_before: list[str] = field(default_factory=list)
    backfill_count: int = 0
    reviewable_count: int = 0


def reviewable_sections(
    contract_sections: list[IndexedChunk],
    *,
    min_chars: int,
) -> list[IndexedChunk]:
    return filter_review_sections(contract_sections, min_chars=min_chars)


def _covered_section_ids(findings: list[ComplianceFinding]) -> set[str]:
    return {
        sid
        for finding in findings
        if (sid := finding.contract_section_id)
    }


def ensure_section_coverage(
    reviewable: list[IndexedChunk],
    findings: list[ComplianceFinding],
    *,
    min_chars: int,
    sections_by_id: dict[str, IndexedChunk] | None = None,
    settings: ReviewSettings | None = None,
) -> SectionCoverageResult:
    """Append explicit gap findings for reviewable sections missing from findings."""
    cfg = settings or get_settings()
    reviewable_ids = {s.section_id for s in reviewable}
    covered = _covered_section_ids(findings)
    uncovered = sorted(reviewable_ids - covered)

    if not uncovered:
        return SectionCoverageResult(
            findings=list(findings),
            reviewable_count=len(reviewable),
        )

    sections_map = sections_by_id or {s.section_id: s for s in reviewable}
    backfill: list[ComplianceFinding] = []
    warnings: list[str] = []

    for section_id in uncovered:
        section = sections_map.get(section_id)
        title = (section.title if section else None) or section_id
        base_rationale = (
            "No finding was produced for this section during compare, merge, "
            "or final gap verify."
        )
        status, review_outcome, suffix = resolve_gap_finding_status(
            section,
            gap_type="coverage_backfill",
            settings=cfg,
        )
        rationale = f"{base_rationale}{suffix}" if suffix else base_rationale
        backfill.append(
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=f"{section_id}:coverage_backfill",
                dimension_label=f"Section {title} — review incomplete",
                status=status,
                severity=Severity.INFO,
                contract_section_id=section_id,
                rationale=rationale,
                metadata={
                    "compliance_mode": "section_first",
                    "gap_type": "coverage_backfill",
                    "review_outcome": review_outcome,
                    "review_min_section_chars": min_chars,
                },
            )
        )
        warnings.append(f"coverage backfill added for section {section_id}")

    return SectionCoverageResult(
        findings=list(findings) + backfill,
        warnings=warnings,
        uncovered_before=uncovered,
        backfill_count=len(backfill),
        reviewable_count=len(reviewable),
    )
