"""Rerank union retrieval hits — cross-encoder with lexical fallback."""

from __future__ import annotations

import re
from typing import Literal

from document_core.embeddings.reranker_service import score_query_passages
from document_core.schemas.chunk import RetrievalHit

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _lexical_score(query: str, passage: str) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    p_tokens = _tokenize(passage)
    if not p_tokens:
        return 0.0
    return len(q_tokens & p_tokens) / len(q_tokens)


def _passage_for_rerank(hit: RetrievalHit, *, max_chars: int) -> str:
    parent = hit.parent_chunk
    title = (parent.title or "").strip()
    text = (parent.text or "").strip()[:max_chars]
    if title and text:
        return f"{title}\n{text}"
    return title or text


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    if hi <= lo:
        return [1.0] * len(scores)
    span = hi - lo
    return [(score - lo) / span for score in scores]


def _lexical_rerank(
    query: str,
    hits: list[RetrievalHit],
    *,
    passages: list[str] | None = None,
) -> list[RetrievalHit]:
    scored: list[tuple[float, RetrievalHit]] = []
    for index, hit in enumerate(hits):
        passage = (
            passages[index]
            if passages is not None
            else (hit.parent_chunk.text or hit.parent_chunk.title or "")
        )
        lex = _lexical_score(query, passage)
        fused = 0.65 * lex + 0.35 * float(hit.score)
        scored.append((fused, hit.model_copy(update={"score": fused})))
    return [hit for _, hit in sorted(scored, key=lambda item: item[0], reverse=True)]


def rerank_hits(
    query: str,
    hits: list[RetrievalHit],
    *,
    top_k: int,
    enabled: bool = True,
    backend: Literal["lexical", "cross_encoder"] = "lexical",
    max_passage_chars: int = 2000,
    fusion_retrieval_weight: float = 0.10,
    usage: dict[str, str] | None = None,
) -> list[RetrievalHit]:
    """Return top_k hits after cross-encoder, lexical fusion, or retrieval score sort."""
    if not hits:
        return []

    limit = max(1, top_k)
    if not enabled:
        if usage is not None:
            usage["reranker_used"] = "off"
        ordered = sorted(hits, key=lambda hit: hit.score, reverse=True)
        return ordered[:limit]

    passages = [_passage_for_rerank(hit, max_chars=max_passage_chars) for hit in hits]

    if backend == "cross_encoder":
        ce_scores = score_query_passages(query, passages)
        if ce_scores is not None and len(ce_scores) == len(hits):
            weight = max(0.0, min(1.0, fusion_retrieval_weight))
            ce_norm = _normalize_scores(ce_scores)
            scored: list[tuple[float, RetrievalHit]] = []
            for ce, hit in zip(ce_norm, hits, strict=True):
                fused = (1.0 - weight) * ce + weight * float(hit.score)
                scored.append((fused, hit.model_copy(update={"score": fused})))
            ordered = [hit for _, hit in sorted(scored, key=lambda item: item[0], reverse=True)]
            if usage is not None:
                usage["reranker_used"] = "cross_encoder"
            return ordered[:limit]
        if usage is not None:
            usage["reranker_used"] = "lexical_fallback"

    ordered = _lexical_rerank(query, hits, passages=passages)
    if usage is not None and "reranker_used" not in usage:
        usage["reranker_used"] = "lexical"
    return ordered[:limit]
