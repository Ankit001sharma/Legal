"""Phase 1 — Source validation engine."""

from __future__ import annotations

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.domains import get_domain_adapter
from deep_research_from_scratch.validation.models import SourceValidation
from deep_research_from_scratch.validation.relevance_scoring import score_relevance


def compute_trust_score(
    authority: int,
    reliability: int,
    freshness: int,
    relevance: int,
) -> int:
    """Weighted blend of source quality dimensions."""
    score = (
        authority * 0.40
        + reliability * 0.30
        + freshness * 0.15
        + relevance * 0.15
    )
    return max(0, min(100, int(round(score))))


def validate_source(
    source: RetrievedSource,
    research_brief: str,
    *,
    relevance_score: int | None = None,
    relevance_reason: str = "",
) -> SourceValidation:
    """Score a single retrieved source."""
    adapter = get_domain_adapter(app_config.VALIDATION_DOMAIN)

    authority = adapter.authority_score(source.url, source.title, source.fetched)
    reliability = adapter.reliability_score(
        source.fetched, source.access_denied, source.excerpt
    )
    freshness = adapter.freshness_score(source.title, source.excerpt)

    if relevance_score is None:
        rel = score_relevance(research_brief, source)
        relevance_score = rel.relevance_score
        relevance_reason = rel.reason

    trust = compute_trust_score(authority, reliability, freshness, relevance_score)
    usable = (
        trust >= app_config.MIN_TRUST_SCORE
        and relevance_score >= app_config.MIN_RELEVANCE_SCORE
        and not source.access_denied
    )
    if not source.fetched and source.access_denied:
        usable = False

    reason_parts = []
    if relevance_reason:
        reason_parts.append(relevance_reason)
    if not usable:
        if trust < app_config.MIN_TRUST_SCORE:
            reason_parts.append(f"trust score {trust} below threshold")
        if relevance_score < app_config.MIN_RELEVANCE_SCORE:
            reason_parts.append(f"relevance {relevance_score} below threshold")
        if source.access_denied:
            reason_parts.append("access denied")

    return SourceValidation(
        source=source.url,
        authority_score=authority,
        relevance_score=relevance_score,
        freshness_score=freshness,
        trust_score=trust,
        usable=usable,
        reason="; ".join(reason_parts) if reason_parts else "acceptable quality",
    )


def validate_sources(
    sources: list[RetrievedSource],
    research_brief: str,
) -> list[tuple[RetrievedSource, SourceValidation]]:
    """Validate all sources and attach scores."""
    results: list[tuple[RetrievedSource, SourceValidation]] = []
    for source in sources:
        validation = validate_source(source, research_brief)
        data = source.model_dump()
        data["validation"] = validation
        updated = RetrievedSource(**data)
        results.append((updated, validation))
    return results
