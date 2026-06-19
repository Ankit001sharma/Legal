"""Integration test: category metadata filter retrieval."""

import pytest
from document_core.schemas.chunk import DocumentKind, IngestRequest, SearchRequest
from document_core.services.ingest import ingest_document
from document_core.services.search import search_policy_by_categories
from document_core.store.pgvector_store import PgVectorDocumentStore


@pytest.mark.asyncio
async def test_category_metadata_search_finds_policy(store: PgVectorDocumentStore):
    tenant = "cat-tenant"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Vendor Security Policy",
            kind=DocumentKind.POLICY,
            text="4. Security Controls\nVendor shall implement encryption and access controls.",
            categories=["vendor_security", "security"],
        ),
        store=store,
    )
    hits = await search_policy_by_categories(
        tenant,
        ["vendor_security"],
        "encryption access control",
        top_k=5,
        store=store,
    )
    assert hits
    assert "encryption" in hits[0].parent_chunk.text.lower()
