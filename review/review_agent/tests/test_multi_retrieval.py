"""Tests for multi-path policy retrieval union and retry ladder."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

from review_agent.config import ReviewSettings
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.services.multi_retrieval import (
    _query_for_attempt,
    _union_hits,
    multi_retrieve_for_section,
)


def _hit(text: str, chunk_id: str, score: float) -> RetrievalHit:
    doc_id = uuid4()
    chunk = IndexedChunk(
        chunk_id=chunk_id,
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id=chunk_id,
        section_path=chunk_id,
        title=chunk_id,
        text=text,
    )
    return RetrievalHit(parent_chunk=chunk, score=score)


def _section() -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="12.2",
        section_path="12.2",
        title="Limitation of Liability",
        text="Total liability shall not exceed twelve months fees.",
    )


def _classification(section: IndexedChunk) -> SectionCategoryResult:
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=["liability"],
        query_terms=["limitation of liability", "Limitation of Liability"],
    )


def test_union_dedupes_by_parent_and_keeps_best_score():
    paths: dict[str, int] = {}
    dense = [_hit("alpha policy", "p1", 0.9)]
    fts = [_hit("alpha policy", "p1", 0.4), _hit("beta keyword match", "p2", 0.6)]
    union = _union_hits(dense, fts, paths=paths)
    assert len(union) == 2
    assert paths["union_count"] == 2
    by_id = {h.parent_chunk.chunk_id: h for h in union}
    assert by_id["p1"].score == 0.9


def test_query_for_attempt_broadens_on_retry():
    section = _section()
    classification = _classification(section)
    q0, _, hard0 = _query_for_attempt(classification, section, 0)
    q1, _, hard1 = _query_for_attempt(classification, section, 1)
    q2, cats2, hard2 = _query_for_attempt(classification, section, 2)
    assert q0 == "limitation of liability"
    assert q1 == "Limitation of Liability"
    assert hard0 and hard1
    assert not hard2
    assert "general" in cats2


@pytest.mark.asyncio
async def test_multi_retrieve_merges_three_paths():
    section = _section()
    dense_hit = _hit("dense only", "d1", 0.5)
    fts_hit = _hit("twelve months fees cap", "f1", 0.7)
    meta_hit = _hit("liability policy section", "m1", 0.6)

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return []

        async def search_policy_recall(self, _req):
            return [dense_hit]

        async def search_policy_fts(self, _req):
            return [fts_hit]

        async def search_policy_by_categories(self, _req, *, categories):
            assert categories
            return [meta_hit]

    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        classification=_classification(section),
    )
    assert bundle.section_id == "12.2"
    assert len(bundle.policy_hits) <= 10
    assert bundle.retrieval_meta.get("dense_count") == 1
    assert bundle.retrieval_meta.get("fts_count") == 1
    assert bundle.retrieval_meta.get("metadata_count") == 1
    assert len(bundle.retrieval_meta.get("attempts") or []) == 1
    ids = {h.parent_chunk.chunk_id for h in bundle.policy_hits}
    assert {"d1", "f1", "m1"}.issubset(ids)


@pytest.mark.asyncio
async def test_multi_retrieve_retries_when_first_attempt_empty():
    section = _section()
    hit = _hit("found on retry", "r1", 0.8)
    recall_calls: list[str] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return []

        async def search_policy_recall(self, req):
            recall_calls.append(req.query)
            if req.query == "Limitation of Liability":
                return [hit]
            return []

        async def search_policy_fts(self, _req):
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(retrieval_max_attempts=3)
    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=_classification(section),
    )
    assert len(bundle.policy_hits) == 1
    assert len(bundle.retrieval_meta["attempts"]) == 2
    assert bundle.retrieval_meta["final_attempt"] == 1
    assert recall_calls[0] == "limitation of liability"
    assert recall_calls[1] == "Limitation of Liability"


@pytest.mark.asyncio
async def test_multi_retrieve_passes_document_ids_when_category_filter_set():
    section = _section()
    scope_id = str(uuid4())
    category_id = uuid4()
    seen_document_ids: list[list] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return [category_id]

        async def search_policy_recall(self, req):
            if req.document_ids is not None:
                seen_document_ids.append(list(req.document_ids))
            return []

        async def search_policy_fts(self, req):
            if req.document_ids is not None:
                seen_document_ids.append(list(req.document_ids))
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(
        retrieval_max_attempts=1,
        retrieval_category_hard_filter=True,
        retrieval_category_filter_fallback=False,
    )
    await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=_classification(section),
        scope_document_ids=[scope_id, str(category_id)],
    )
    assert seen_document_ids
    assert str(category_id) in {str(doc_id) for doc_id in seen_document_ids[0]}
