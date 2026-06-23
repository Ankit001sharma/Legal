"""Unit tests for source validation (Phase 1)."""

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.source_validation import (
    compute_trust_score,
    validate_source,
    validate_sources,
)


def test_compute_trust_score_weighted():
    score = compute_trust_score(90, 80, 70, 60)
    assert 70 <= score <= 85


def test_validate_primary_source_high_trust():
    source = RetrievedSource(
        url="https://indiankanoon.org/doc/1/",
        title="Test Case v State",
        authority_tier="primary",
        fetched=True,
        excerpt="The court held that bail may be granted when conditions are met.",
    )
    result = validate_source(source, "bail conditions under criminal law")
    assert result.authority_score >= 80
    assert result.trust_score >= 40
    assert result.usable is True


def test_validate_paywall_access_denied_not_usable():
    source = RetrievedSource(
        url="https://www.manupatra.com/doc/1/",
        title="Paywalled",
        fetched=False,
        access_denied=True,
        excerpt="",
    )
    result = validate_source(source, "contract law")
    assert result.usable is False


def test_validate_sources_attaches_validation():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Case A",
            authority_tier="primary",
            fetched=True,
            excerpt="Section 420 IPC cheating offence elements.",
        )
    ]
    pairs = validate_sources(sources, "cheating under section 420")
    updated, validation = pairs[0]
    assert updated.validation is not None
    assert validation.source == sources[0].url
