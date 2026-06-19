"""Policy tombstone delete (P2.3)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from document_core.schemas.chunk import DocumentKind, IngestRequest, SearchRequest
from document_core.schemas.registry import (
    DeletePolicyRequest,
    ListPolicyRegistryRequest,
    RegisterPolicyRequest,
)
from document_core.services.ingest import ingest_document
from document_core.services.registry import delete_policy, list_policy_registry, register_policy
from document_core.services.search import list_policy_ids_by_categories, search_policy
from document_core.store.pgvector_store import PgVectorDocumentStore


@pytest.mark.asyncio
async def test_delete_policy_tombstone_excludes_search(store: PgVectorDocumentStore):
    tenant = "del-tenant"
    policy_ref = "vendor-indemnity-standard"
    register_policy(
        RegisterPolicyRequest(
            tenant_id=tenant,
            policy_ref=policy_ref,
            title="Vendor Indemnity",
            policy_type="vendor",
        ),
        store=store,
    )
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Vendor Indemnity",
            kind=DocumentKind.POLICY,
            text="3. Indemnification\nVendor shall indemnify Customer.",
            metadata={"policy_ref": policy_ref, "categories": ["indemnification"]},
            categories=["indemnification"],
        ),
        store=store,
    )
    hits_before = await search_policy(
        SearchRequest(tenant_id=tenant, query="indemnify", kind=DocumentKind.POLICY),
        store=store,
    )
    assert hits_before

    result = delete_policy(
        DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref),
        store=store,
    )
    assert result.index_status == "deleted"

    hits_after = await search_policy(
        SearchRequest(tenant_id=tenant, query="indemnify", kind=DocumentKind.POLICY),
        store=store,
    )
    assert not hits_after


@pytest.mark.asyncio
async def test_delete_policy_category_filter_excludes_deleted(store: PgVectorDocumentStore):
    tenant = "del-cat-tenant"
    policy_ref = "liability-cap"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Liability Policy",
            kind=DocumentKind.POLICY,
            text="4. Limitation\nLiability capped at fees.",
            metadata={"policy_ref": policy_ref, "categories": ["liability"]},
            categories=["liability"],
        ),
        store=store,
    )
    doc_ids_before = await list_policy_ids_by_categories(
        tenant,
        ["liability"],
        store=store,
    )
    assert doc_ids_before

    delete_policy(DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref), store=store)

    doc_ids_after = await list_policy_ids_by_categories(
        tenant,
        ["liability"],
        store=store,
    )
    assert not doc_ids_after


@pytest.mark.asyncio
async def test_list_policy_registry_omits_deleted_by_default(store: PgVectorDocumentStore):
    tenant = "list-tenant"
    policy_ref = "retired-playbook"
    register_policy(
        RegisterPolicyRequest(tenant_id=tenant, policy_ref=policy_ref, title="Retired"),
        store=store,
    )
    delete_policy(DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref), store=store)

    default_list = list_policy_registry(ListPolicyRegistryRequest(tenant_id=tenant), store=store)
    assert all(p.policy_ref != policy_ref for p in default_list.policies)

    deleted_list = list_policy_registry(
        ListPolicyRegistryRequest(tenant_id=tenant, index_status="deleted"),
        store=store,
    )
    assert any(p.policy_ref == policy_ref for p in deleted_list.policies)


@pytest.mark.asyncio
async def test_reindex_restores_deleted_to_indexed(store: PgVectorDocumentStore):
    tenant = "reindex-tenant"
    policy_ref = "refresh-policy"
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Refresh Policy",
            kind=DocumentKind.POLICY,
            text="1. Term\nOne year.",
            metadata={"policy_ref": policy_ref},
        ),
        store=store,
    )
    delete_policy(DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref), store=store)

    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Refresh Policy v2",
            kind=DocumentKind.POLICY,
            text="1. Term\nTwo years.",
            metadata={"policy_ref": policy_ref},
        ),
        store=store,
    )
    from document_core.services.registry import get_policy_by_ref

    row = get_policy_by_ref(tenant, policy_ref, store=store)
    assert row is not None
    assert row.index_status == "indexed"


@pytest.mark.asyncio
async def test_list_sections_raises_for_deleted_document(store: PgVectorDocumentStore):
    from document_core.schemas.chunk import ListSectionsRequest
    from document_core.services.search import list_sections

    tenant = "sections-del"
    policy_ref = "deleted-doc"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Deleted Doc",
            kind=DocumentKind.POLICY,
            text="1. Scope\nApplies to all.",
            metadata={"policy_ref": policy_ref},
        ),
        store=store,
    )
    delete_policy(DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref), store=store)

    with pytest.raises(ValueError, match="document deleted"):
        await list_sections(
            ListSectionsRequest(tenant_id=tenant, document_id=result.document_id),
            store=store,
        )


@pytest.mark.asyncio
async def test_tombstone_keeps_chunks_in_db(store: PgVectorDocumentStore):
    tenant = "chunk-tenant"
    policy_ref = "chunk-retain"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Chunk Retain",
            kind=DocumentKind.POLICY,
            text="2. Payment\nNet 30 days.",
            metadata={"policy_ref": policy_ref},
        ),
        store=store,
    )
    delete_policy(DeletePolicyRequest(tenant_id=tenant, policy_ref=policy_ref), store=store)

    with store.engine.connect() as conn:
        count = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM document_chunks
                WHERE tenant_id = :tenant_id AND document_id = :document_id
                """
            ),
            {"tenant_id": tenant, "document_id": result.document_id},
        ).scalar()
    assert count and count > 0
