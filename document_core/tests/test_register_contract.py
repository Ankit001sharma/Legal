"""Contract registry (P2.2)."""

from __future__ import annotations

import pytest

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestSectionInput
from document_core.schemas.registry import RegisterContractRequest
from document_core.services.ingest import ingest_document
from document_core.services.registry import (
    get_contract_by_ref,
    get_policy_by_ref,
    register_contract,
    stable_contract_document_id,
)
from document_core.store.pgvector_store import PgVectorDocumentStore


def test_stable_contract_document_id():
    tenant = "acme"
    ref = "acme-vendor-msa-2026"
    first = stable_contract_document_id(tenant, ref)
    second = stable_contract_document_id(tenant, ref)
    assert first == second
    assert stable_contract_document_id(tenant, ref, first) == first


@pytest.mark.asyncio
async def test_register_contract_pending(store: PgVectorDocumentStore):
    record = register_contract(
        RegisterContractRequest(
            tenant_id="acme",
            contract_ref="acme-vendor-msa-2026",
            title="Vendor MSA v3",
            contract_type="msa",
            metadata={"parties": ["Acme", "Vendor Co"]},
        ),
        store=store,
    )
    assert record.index_status == "pending"
    assert record.kind == "contract"
    assert record.policy_ref == "acme-vendor-msa-2026"
    assert record.metadata.get("contract_ref") == "acme-vendor-msa-2026"
    assert record.metadata.get("contract_type") == "msa"

    fetched = get_contract_by_ref("acme", "acme-vendor-msa-2026", store=store)
    assert fetched is not None
    assert fetched.document_id == record.document_id

    policy_lookup = get_policy_by_ref("acme", "acme-vendor-msa-2026", store=store)
    assert policy_lookup is not None
    assert policy_lookup.kind == "contract"


@pytest.mark.asyncio
async def test_register_contract_ingest_indexed(store: PgVectorDocumentStore):
    tenant = "acme2"
    contract_ref = "acme-nda-2026"
    document_id = stable_contract_document_id(tenant, contract_ref)
    register_contract(
        RegisterContractRequest(
            tenant_id=tenant,
            contract_ref=contract_ref,
            title="NDA",
            document_id=document_id,
            contract_type="nda",
        ),
        store=store,
    )
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=document_id,
            title="NDA",
            kind=DocumentKind.CONTRACT,
            sections=[
                IngestSectionInput(section_id="1", title="Confidentiality", text="Keep secrets."),
            ],
            metadata={"contract_ref": contract_ref},
        ),
        store=store,
    )
    row = get_contract_by_ref(tenant, contract_ref, store=store)
    assert row is not None
    assert row.index_status == "indexed"


def test_get_contract_by_ref_wrong_kind_returns_none(store: PgVectorDocumentStore):
    from document_core.schemas.registry import RegisterPolicyRequest
    from document_core.services.registry import register_policy

    register_policy(
        RegisterPolicyRequest(
            tenant_id="t-policy",
            policy_ref="vendor-indemnity",
            title="Vendor Indemnity",
        ),
        store=store,
    )
    assert get_contract_by_ref("t-policy", "vendor-indemnity", store=store) is None
