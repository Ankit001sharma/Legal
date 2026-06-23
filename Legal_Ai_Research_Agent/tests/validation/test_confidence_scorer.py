"""Unit tests for confidence scoring (Phase 9)."""

from deep_research_from_scratch.validation.confidence_scorer import compute_confidence
from deep_research_from_scratch.validation.models import (
    CitationCoverageReport,
    CoverageReport,
    SourceValidation,
    ValidatedClaim,
)


def test_confidence_high_with_strong_evidence():
    validations = [
        SourceValidation(
            source="https://indiankanoon.org/doc/1/",
            authority_score=90,
            relevance_score=85,
            freshness_score=80,
            trust_score=88,
            usable=True,
            reason="ok",
        )
    ]
    citation = CitationCoverageReport(
        total_claims=2, supported_claims=2, unsupported_claims=0, coverage_pct=100.0
    )
    coverage = CoverageReport(coverage_pct=87.5)
    claims = [
        ValidatedClaim(
            claim="Supported claim one.",
            support_level="direct",
            source_count=2,
            confidence=90,
            consensus="high",
        ),
        ValidatedClaim(
            claim="Supported claim two.",
            support_level="direct",
            source_count=1,
            confidence=85,
            consensus="medium",
        ),
    ]
    metrics = compute_confidence(validations, citation, coverage, claims, 85.0)
    assert metrics.overall_confidence_pct >= 70
    assert metrics.citation_coverage_pct == 100.0
