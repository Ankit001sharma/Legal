"""Phase 4 — Retrieval relevance scoring."""

from __future__ import annotations

from pydantic import BaseModel, Field

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.domains import get_domain_adapter


class RelevanceResult(BaseModel):
    relevance_score: int = Field(ge=0, le=100)
    reason: str = ""
    usable: bool = True


def score_relevance(research_brief: str, source: RetrievedSource) -> RelevanceResult:
    """Deterministic keyword overlap relevance between brief and source."""
    adapter = get_domain_adapter(app_config.VALIDATION_DOMAIN)
    brief_kw = adapter.extract_keywords(research_brief)
    source_text = f"{source.title} {source.excerpt} {source.citation or ''}"
    source_kw = adapter.extract_keywords(source_text)

    if not brief_kw:
        return RelevanceResult(
            relevance_score=50,
            reason="no brief keywords to compare",
            usable=True,
        )

    overlap = brief_kw & source_kw
    ratio = len(overlap) / max(len(brief_kw), 1)
    score = int(min(100, max(10, ratio * 100 + (20 if source.fetched else 0))))

    # Boost when title contains brief terms
    title_lower = (source.title or "").lower()
    title_hits = sum(1 for kw in brief_kw if kw in title_lower)
    score = min(100, score + title_hits * 5)

    usable = score >= app_config.MIN_RELEVANCE_SCORE
    reason = (
        f"keyword overlap {len(overlap)}/{len(brief_kw)} terms"
        if overlap
        else "low keyword overlap with research brief"
    )
    return RelevanceResult(relevance_score=score, reason=reason, usable=usable)
