"""Phase 10 — Research gap detection."""

from __future__ import annotations

from deep_research_from_scratch.source_registry import RetrievedSource, count_fetches
from deep_research_from_scratch.validation.domains import get_domain_adapter
from deep_research_from_scratch.validation.models import CoverageReport, ValidatedClaim
from deep_research_from_scratch.config import config as app_config


def detect_research_gaps(
    research_brief: str,
    coverage_report: CoverageReport,
    claims: list[ValidatedClaim],
    sources: list[RetrievedSource],
    findings: str = "",
    report: str = "",
) -> list[str]:
    """Identify areas where evidence is missing."""
    gaps: list[str] = list(coverage_report.missing_areas)

    unsupported = [c.claim for c in claims if c.support_level == "unsupported"]
    if unsupported:
        gaps.append(
            f"Insufficient evidence for {len(unsupported)} claim(s) in the report."
        )

    _, primary_fetches = count_fetches(sources)
    if primary_fetches == 0 and sources:
        gaps.append("No primary-tier sources were successfully fetched.")

    conflicting = [c for c in claims if c.consensus == "conflicting"]
    if conflicting:
        gaps.append(
            f"Conflicting evidence across sources for {len(conflicting)} finding(s)."
        )

    adapter = get_domain_adapter(app_config.VALIDATION_DOMAIN)
    if hasattr(adapter, "required_landmarks"):
        landmarks = adapter.required_landmarks(research_brief, findings, report)
        gaps.extend(f"Missing landmark authority: {lm}" for lm in landmarks)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for gap in gaps:
        if gap not in seen:
            seen.add(gap)
            unique.append(gap)
    return unique
