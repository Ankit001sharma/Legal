"""Phase 35A — parent category search and boost."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    IndexedChunk,
    IngestRequest,
    RetrievalHit,
)
from document_core.services.ingest import ingest_document
from document_core.services.search import (
    boost_parent_category_hits,
    search_policy_by_categories,
)
from document_core.store.pgvector_store import PgVectorDocumentStore
from tests.fixtures import SAMPLE_POLICY


def test_boost_parent_category_hits_ranks_liability_first():
    doc_id = uuid4()
    liability_parent = IndexedChunk(
        chunk_id=f"{doc_id}:1",
        document_id=doc_id,
        tenant_id="t",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        parent_id=None,
        section_id="1",
        section_path="1",
        title="Limitation of Liability",
        text="Cap on liability.",
        metadata={"categories": ["liability"]},
    )
    indemnity_parent = IndexedChunk(
        chunk_id=f"{doc_id}:2",
        document_id=doc_id,
        tenant_id="t",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        parent_id=None,
        section_id="2",
        section_path="2",
        title="Indemnification",
        text="Indemnify customer.",
        metadata={"categories": ["indemnity"]},
    )
    hits = [
        RetrievalHit(parent_chunk=indemnity_parent, score=0.9, matched_child_ids=[]),
        RetrievalHit(parent_chunk=liability_parent, score=0.85, matched_child_ids=[]),
    ]
    boosted = boost_parent_category_hits(hits, ["liability"], boost=0.15)
    assert boosted[0].parent_chunk.section_id == "1"
    assert "liability" in boosted[0].parent_chunk.metadata["categories"]


@pytest.mark.asyncio
async def test_search_policy_by_categories_boosts_matching_parent(store: PgVectorDocumentStore):
    tenant = "boost-parent"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Playbook",
            kind=DocumentKind.POLICY,
            text=SAMPLE_POLICY,
        ),
        store=store,
    )
    hits = await search_policy_by_categories(
        tenant,
        ["liability"],
        "limitation liability cap fees twelve months",
        store=store,
    )
    assert hits
    top_cats = hits[0].parent_chunk.metadata.get("categories") or []
    assert "liability" in top_cats


@pytest.mark.asyncio
async def test_list_document_ids_matches_parent_chunk_categories(
    store: PgVectorDocumentStore,
    pg_engine,
):
    tenant = "parent-cat-sql"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Playbook",
            kind=DocumentKind.POLICY,
            text=SAMPLE_POLICY,
        ),
        store=store,
    )
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE policy_documents
                SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{categories}', '[]'::jsonb)
                WHERE tenant_id = :tenant_id AND document_id = :document_id
                """
            ),
            {"tenant_id": tenant, "document_id": result.document_id},
        )
    doc_ids = store.list_document_ids_by_categories(tenant, ["liability"])
    assert result.document_id in doc_ids
