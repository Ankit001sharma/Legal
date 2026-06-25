"""Policy freshness (Phase 28 T4)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.services.ingest import ingest_document
from document_core.store.pgvector_store import PgVectorDocumentStore


@pytest.mark.asyncio
async def test_save_document_sets_last_verified_at(store: PgVectorDocumentStore):
    tenant = "freshness-tenant"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Liability Policy",
            kind=DocumentKind.POLICY,
            text="4. Limitation of Liability\nCap is fees paid.",
            categories=["liability"],
        ),
        store=store,
    )
    with store.engine.connect() as conn:
        verified = conn.execute(
            text(
                """
                SELECT last_verified_at IS NOT NULL
                FROM policy_documents
                WHERE tenant_id = :tenant_id AND document_id = :document_id
                """
            ),
            {"tenant_id": tenant, "document_id": result.document_id},
        ).scalar()
    assert verified is True


@pytest.mark.asyncio
async def test_list_document_ids_excludes_stale_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    store: PgVectorDocumentStore,
):
    from document_core.config import DocumentCoreSettings, get_settings

    tenant = "stale-filter-tenant"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Stale Liability Policy",
            kind=DocumentKind.POLICY,
            text="4. Limitation of Liability\nCap is fees paid.",
            categories=["liability"],
        ),
        store=store,
    )
    with store.engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE policy_documents
                SET last_verified_at = now() - interval '30 days'
                WHERE tenant_id = :tenant_id AND document_id = :document_id
                """
            ),
            {"tenant_id": tenant, "document_id": result.document_id},
        )

    get_settings.cache_clear()
    stale_settings = DocumentCoreSettings(policy_stale_days=7)
    monkeypatch.setattr("document_core.config.get_settings", lambda: stale_settings)
    monkeypatch.setattr("document_core.store.pgvector_store.get_settings", lambda: stale_settings)

    doc_ids = store.list_document_ids_by_categories(tenant, ["liability"])
    assert result.document_id not in doc_ids

    get_settings.cache_clear()
