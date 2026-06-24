"""Rerank retrieval hits — lexical fusion when enabled."""

from __future__ import annotations

import re

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


def rerank_hits(
    query: str,
    hits: list[RetrievalHit],
    *,
    top_k: int,
    enabled: bool = False,
) -> list[RetrievalHit]:
    """Return top_k hits; fuse lexical overlap with retrieval score when enabled."""
    if not hits:
        return []

    if enabled:
        scored: list[tuple[float, RetrievalHit]] = []
        for hit in hits:
            passage = hit.parent_chunk.text or hit.parent_chunk.title or ""
            lex = _lexical_score(query, passage)
            fused = 0.65 * lex + 0.35 * float(hit.score)
            scored.append((fused, hit.model_copy(update={"score": fused})))
        ordered = [h for _, h in sorted(scored, key=lambda x: x[0], reverse=True)]
    else:
        ordered = sorted(hits, key=lambda h: h.score, reverse=True)

    return ordered[: max(1, top_k)]
