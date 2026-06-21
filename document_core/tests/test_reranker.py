"""Tests for cross-encoder reranker with lexical fallback."""

from uuid import uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.search.reranker import _passage_for_rerank, rerank_hits


def _hit(text: str, score: float, *, title: str = "t") -> RetrievalHit:
    return RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="c",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="1",
            section_path="1",
            title=title,
            text=text,
        ),
        score=score,
    )


def test_reranker_disabled_preserves_order():
    hits = [_hit("alpha beta", 0.9), _hit("gamma delta", 0.5)]
    usage: dict[str, str] = {}
    out = rerank_hits("alpha", hits, top_k=1, enabled=False, usage=usage)
    assert len(out) == 1
    assert out[0].parent_chunk.text.startswith("alpha")
    assert usage["reranker_used"] == "off"


def test_reranker_lexical_prefers_match():
    hits = [_hit("unrelated content", 0.95), _hit("limitation of liability cap", 0.4)]
    out = rerank_hits(
        "limitation of liability",
        hits,
        top_k=1,
        enabled=True,
        backend="lexical",
    )
    assert "liability" in out[0].parent_chunk.text


def test_passage_includes_title():
    hit = _hit("body text about liability caps", 0.5, title="Limitation of Liability")
    passage = _passage_for_rerank(hit, max_chars=200)
    assert "Limitation of Liability" in passage
    assert "body text" in passage


def test_cross_encoder_reorders_by_mock_scores(monkeypatch):
    hits = [_hit("unrelated privacy policy text", 0.95), _hit("forced labor due diligence", 0.4)]

    def _fake_scores(_query: str, passages: list[str]) -> list[float]:
        assert len(passages) == 2
        if "forced labor" in passages[1]:
            return [0.1, 0.9]
        return [0.1, 0.9]

    monkeypatch.setattr(
        "document_core.search.reranker.score_query_passages",
        _fake_scores,
    )
    usage: dict[str, str] = {}
    out = rerank_hits(
        "forced labor human rights",
        hits,
        top_k=1,
        enabled=True,
        backend="cross_encoder",
        usage=usage,
    )
    assert "forced labor" in out[0].parent_chunk.text
    assert usage["reranker_used"] == "cross_encoder"


def test_cross_encoder_fallback_to_lexical(monkeypatch):
    hits = [_hit("unrelated content", 0.95), _hit("limitation of liability cap", 0.4)]

    monkeypatch.setattr(
        "document_core.search.reranker.score_query_passages",
        lambda *_args, **_kwargs: None,
    )
    usage: dict[str, str] = {}
    out = rerank_hits(
        "limitation of liability",
        hits,
        top_k=1,
        enabled=True,
        backend="cross_encoder",
        usage=usage,
    )
    assert "liability" in out[0].parent_chunk.text
    assert usage["reranker_used"] == "lexical_fallback"
