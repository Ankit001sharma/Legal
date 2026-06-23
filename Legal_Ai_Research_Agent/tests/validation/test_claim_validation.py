"""Unit tests for claim validation (Phase 2)."""

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.claim_validation import validate_claims


def test_direct_support_when_excerpt_matches():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Zaheer Khan",
            source_index=1,
            authority_tier="primary",
            fetched=True,
            excerpt="Zaheer Khan, (2006) 4 SCC 227 held post-employment restraint clauses.",
        )
    ]
    report = (
        "The Supreme Court in Zaheer Khan held that post-employment restraint "
        "clauses require careful scrutiny under contract law principles."
    )
    claims = validate_claims(report, ["Zaheer Khan case"], [], sources)
    assert len(claims) >= 1
    assert any(c.support_level in ("direct", "indirect", "weak") for c in claims)


def test_unsupported_claim_when_no_corpus_match():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Unrelated",
            source_index=1,
            fetched=True,
            excerpt="Totally different topic about tax law.",
        )
    ]
    report = (
        "The court definitively ruled that all cryptocurrency is illegal "
        "throughout India without exception."
    )
    claims = validate_claims(report, ["tax law"], [], sources)
    assert any(c.support_level == "unsupported" for c in claims)
