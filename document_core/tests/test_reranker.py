"""Tests for lexical-fusion reranker."""

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.search.reranker import rerank_hits


def _hit(text: str, score: float) -> RetrievalHit:
    return RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="c",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="1",
            section_path="1",
            title="t",
            text=text,
        ),
        score=score,
    )


def test_reranker_disabled_preserves_order():
    hits = [_hit("alpha beta", 0.9), _hit("gamma delta", 0.5)]
    out = rerank_hits("alpha", hits, top_k=1, enabled=False)
    assert len(out) == 1
    assert out[0].parent_chunk.text.startswith("alpha")


def test_reranker_enabled_prefers_lexical_match():
    hits = [_hit("unrelated content", 0.95), _hit("limitation of liability cap", 0.4)]
    out = rerank_hits("limitation of liability", hits, top_k=1, enabled=True)
    assert "liability" in out[0].parent_chunk.text
