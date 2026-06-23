"""Unit tests for report sanitization."""

from deep_research_from_scratch.state_scope import VerificationResult
from deep_research_from_scratch.validation.models import (
    ResearchQualityMetrics,
    ValidatedClaim,
)
from deep_research_from_scratch.validation.pipeline import sanitize_report


def test_unsupported_claim_replaced_with_uncertain():
    report = (
        "The court held that all tokens are illegal. "
        "This is a supported statement about bail procedure."
    )
    claims = [
        ValidatedClaim(
            claim="The court held that all tokens are illegal.",
            support_level="unsupported",
            source_count=0,
            confidence=10,
        ),
        ValidatedClaim(
            claim="This is a supported statement about bail procedure.",
            support_level="direct",
            source_count=1,
            confidence=80,
        ),
    ]
    metrics = ResearchQualityMetrics(
        overall_confidence_pct=55.0,
        confidence_reasoning=["Moderate source quality."],
        research_gaps=["Insufficient evidence on token legality."],
    )
    result = sanitize_report(report, claims, metrics, VerificationResult(passed=False))
    assert "all tokens are illegal" not in result
    assert "[UNCERTAIN: insufficient evidence]" in result
    assert "Confidence Assessment" in result
    assert "Research Gaps" in result
