"""Unit tests for relevance scoring (Phase 4)."""

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.relevance_scoring import score_relevance


def test_high_relevance_on_keyword_overlap():
    source = RetrievedSource(
        url="https://indiankanoon.org/doc/1/",
        title="Bail under BNS Section 480",
        fetched=True,
        excerpt="Anticipatory bail conditions and procedure under Bharatiya Nyaya Sanhita.",
    )
    result = score_relevance("anticipatory bail under BNS", source)
    assert result.relevance_score >= 60
    assert result.usable is True


def test_low_relevance_unrelated_source():
    source = RetrievedSource(
        url="https://example.com/article",
        title="Cooking recipes",
        excerpt="How to bake bread at home.",
    )
    result = score_relevance("murder bail anticipatory custody", source)
    assert result.relevance_score < 60
    assert result.usable is False
