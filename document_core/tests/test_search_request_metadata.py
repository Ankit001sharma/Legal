"""P0-1 regression: SearchRequest.metadata.categories contract for category retrieval."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest, SearchRequest
from document_core.services.ingest import ingest_document
from document_core.services.search import search_policy_by_categories
from document_core.store.pgvector_store import PgVectorDocumentStore


def test_search_request_accepts_metadata_categories() -> None:
    req = SearchRequest(
        tenant_id="t",
        query="limitation of liability cap",
        kind=DocumentKind.POLICY,
        contract_type="nda",
        metadata={"categories": ["liability"]},
    )
    dumped = req.model_dump(mode="json")
    assert dumped["metadata"]["categories"] == ["liability"]
    round_trip = SearchRequest.model_validate(dumped)
    assert round_trip.metadata.get("categories") == ["liability"]


def test_search_request_metadata_defaults_empty() -> None:
    req = SearchRequest(tenant_id="t", query="q")
    assert req.metadata == {}


@pytest.mark.asyncio
async def test_search_policy_by_categories_service_uses_metadata(store: PgVectorDocumentStore) -> None:
    tenant = "meta-cat-tenant"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Liability Playbook",
            kind=DocumentKind.POLICY,
            text="3. Limitation of Liability\nCap shall not exceed fees paid in twelve months.",
            categories=["liability"],
        ),
        store=store,
    )
    hits = await search_policy_by_categories(
        tenant,
        ["liability"],
        "limitation liability cap fees",
        contract_type="nda",
        top_k=5,
        store=store,
    )
    assert hits
    assert "liability" in hits[0].parent_chunk.text.lower()


@pytest.mark.asyncio
async def test_search_policy_by_categories_http_not_500(store: PgVectorDocumentStore) -> None:
    """HTTP handler must not 500 when metadata.categories is present (P0-1)."""
    pytest.importorskip("mcp.document_server.main")
    from mcp.document_server.main import app

    tenant = "http-meta-tenant"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="HTTP Liability Policy",
            kind=DocumentKind.POLICY,
            text="Limitation of Liability: fees paid in the prior twelve months.",
            categories=["liability"],
        ),
        store=store,
    )
    payload = {
        "tenant_id": tenant,
        "query": "limitation of liability",
        "kind": "policy",
        "contract_type": "nda",
        "top_k": 5,
        "metadata": {"categories": ["liability"]},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/tools/search_policy_by_categories", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "results" in body
