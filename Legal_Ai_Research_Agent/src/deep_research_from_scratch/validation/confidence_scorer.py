"""Phase 9 — Confidence scoring engine."""

from __future__ import annotations

from deep_research_from_scratch.validation.models import (
    CitationCoverageReport,
    CoverageReport,
    ResearchQualityMetrics,
    SourceValidation,
    ValidatedClaim,
)
from deep_research_from_scratch.validation.consensus_engine import consensus_score


def compute_confidence(
    source_validations: list[SourceValidation],
    citation_report: CitationCoverageReport,
    coverage_report: CoverageReport,
    claims: list[ValidatedClaim],
    consensus_pct: float,
) -> ResearchQualityMetrics:
    """Compute overall confidence and sub-metrics."""
    total_claims = len(claims) or 1
    unsupported = sum(1 for c in claims if c.support_level == "unsupported")
    unsupported_pct = round(unsupported / total_claims * 100, 1)

    hallucination_rate = unsupported_pct  # aligned with unsupported claims

    if source_validations:
        source_quality = sum(v.trust_score for v in source_validations) / len(
            source_validations
        )
        relevance = sum(v.relevance_score for v in source_validations) / len(
            source_validations
        )
    else:
        source_quality = 50.0
        relevance = 50.0

    coverage_pct = coverage_report.coverage_pct
    citation_pct = citation_report.coverage_pct

    overall = (
        0.25 * source_quality
        + 0.25 * citation_pct
        + 0.20 * consensus_pct
        + 0.15 * coverage_pct
        + 0.15 * (100 - unsupported_pct)
    )
    overall = round(max(0.0, min(100.0, overall)), 1)

    reasoning: list[str] = []
    if source_quality >= 70:
        reasoning.append(f"Source quality is strong ({source_quality:.0f}/100).")
    else:
        reasoning.append(f"Source quality is moderate ({source_quality:.0f}/100).")
    reasoning.append(
        f"{citation_report.supported_claims}/{citation_report.total_claims} claims have citation support."
    )
    if unsupported:
        reasoning.append(f"{unsupported} claim(s) lack sufficient evidence.")
    conflicting = sum(1 for c in claims if c.consensus == "conflicting")
    if conflicting:
        reasoning.append(f"{conflicting} claim(s) have conflicting source evidence.")

    return ResearchQualityMetrics(
        citation_coverage_pct=citation_pct,
        unsupported_claim_pct=unsupported_pct,
        hallucination_rate_pct=hallucination_rate,
        source_quality_score=round(source_quality, 1),
        relevance_score=round(relevance, 1),
        coverage_completeness_pct=coverage_pct,
        consensus_score=consensus_pct,
        overall_confidence_pct=overall,
        confidence_reasoning=reasoning,
        citation_report=citation_report,
        coverage_report=coverage_report,
    )
