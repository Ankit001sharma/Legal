"""Unit tests for citation verification (Phase 5)."""

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.citation_verification import verify_citations
from deep_research_from_scratch.validation.models import ValidatedClaim


def test_citation_coverage_with_supported_claims():
    findings = "Zaheer Khan, (2006) 4 SCC 227."
    report = "As held in Zaheer Khan, (2006) 4 SCC 227, the rule applies."
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Zaheer Khan",
            source_index=1,
            fetched=True,
            excerpt="Zaheer Khan, (2006) 4 SCC 227",
        )
    ]
    claims = [
        ValidatedClaim(
            claim="As held in Zaheer Khan, (2006) 4 SCC 227, the rule applies.",
            support_level="direct",
            source_count=1,
            confidence=85,
        )
    ]
    report_data = verify_citations(report, findings, sources, claims)
    assert report_data.total_claims == 1
    assert report_data.supported_claims == 1
    assert report_data.coverage_pct >= 85.0
