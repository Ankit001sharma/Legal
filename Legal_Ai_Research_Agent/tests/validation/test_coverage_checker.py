"""Unit tests for coverage checker (Phase 6)."""

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.coverage_checker import (
    build_gap_queries,
    check_coverage,
)
from deep_research_from_scratch.validation.models import SourceValidation


def test_coverage_detects_gaps():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Case on bail",
            source_index=1,
            fetched=True,
            excerpt="Bail granted when flight risk is low.",
            validation=SourceValidation(
                source="https://indiankanoon.org/doc/1/",
                authority_score=90,
                relevance_score=80,
                freshness_score=70,
                trust_score=85,
                usable=True,
                reason="ok",
            ),
        )
    ]
    report = check_coverage("anticipatory bail conditions", sources, ["bail notes"])
    assert report.coverage_pct < 100
    assert len(report.missing_areas) >= 1


def test_gap_queries_generated_when_coverage_low():
    coverage = check_coverage("crypto regulation India", [], [])
    queries = build_gap_queries(coverage, "crypto regulation India")
    assert isinstance(queries, list)
