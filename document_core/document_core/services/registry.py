"""Policy registry: metadata rows in policy_documents (source-agnostic)."""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from document_core.schemas.registry import (
    DeletePolicyRequest,
    DeletePolicyResult,
    ListPolicyRegistryRequest,
    ListPolicyRegistryResponse,
    PolicyRegistryRecord,
    RegisterContractRequest,
    RegisterPolicyRequest,
)
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


def stable_policy_document_id(
    tenant_id: str,
    policy_ref: str,
    provided: UUID | None = None,
) -> UUID:
    if provided is not None:
        return provided
    return uuid5(NAMESPACE_DNS, f"{tenant_id}:{policy_ref}")


def stable_contract_document_id(
    tenant_id: str,
    contract_ref: str,
    provided: UUID | None = None,
) -> UUID:
    if provided is not None:
        return provided
    return uuid5(NAMESPACE_DNS, f"{tenant_id}:contract:{contract_ref}")


def register_policy(
    request: RegisterPolicyRequest,
    *,
    store: DocumentStore | None = None,
    kind: str = "policy",
) -> PolicyRegistryRecord:
    doc_store = store or get_store()
    document_id = stable_policy_document_id(
        request.tenant_id,
        request.policy_ref,
        request.document_id,
    )
    return doc_store.upsert_policy_registry(
        tenant_id=request.tenant_id,
        document_id=document_id,
        policy_ref=request.policy_ref,
        title=request.title,
        kind=kind,
        policy_type=request.policy_type,
        source=request.source,
        metadata=request.metadata,
        index_status="pending",
    )


def register_contract(
    request: RegisterContractRequest,
    *,
    store: DocumentStore | None = None,
) -> PolicyRegistryRecord:
    doc_store = store or get_store()
    document_id = stable_contract_document_id(
        request.tenant_id,
        request.contract_ref,
        request.document_id,
    )
    meta = {**request.metadata, "contract_ref": request.contract_ref}
    if request.contract_type:
        meta["contract_type"] = request.contract_type
    return doc_store.upsert_policy_registry(
        tenant_id=request.tenant_id,
        document_id=document_id,
        policy_ref=request.contract_ref,
        title=request.title,
        kind="contract",
        policy_type=request.contract_type,
        source=request.source,
        metadata=meta,
        index_status="pending",
    )


def get_policy_by_ref(
    tenant_id: str,
    policy_ref: str,
    *,
    store: DocumentStore | None = None,
) -> PolicyRegistryRecord | None:
    doc_store = store or get_store()
    return doc_store.get_policy_by_ref(tenant_id, policy_ref)


def get_contract_by_ref(
    tenant_id: str,
    contract_ref: str,
    *,
    store: DocumentStore | None = None,
) -> PolicyRegistryRecord | None:
    record = get_policy_by_ref(tenant_id, contract_ref, store=store)
    if record is None or record.kind != "contract":
        return None
    return record


def delete_policy(
    request: DeletePolicyRequest,
    *,
    store: DocumentStore | None = None,
) -> DeletePolicyResult:
    doc_store = store or get_store()
    record = doc_store.tombstone_policy_by_ref(request.tenant_id, request.policy_ref)
    if record is None:
        raise ValueError(f"policy not found: {request.policy_ref}")
    return DeletePolicyResult(
        tenant_id=request.tenant_id,
        policy_ref=request.policy_ref,
        document_id=record.document_id,
        index_status="deleted",
    )


def list_policy_registry(
    request: ListPolicyRegistryRequest,
    *,
    store: DocumentStore | None = None,
) -> ListPolicyRegistryResponse:
    doc_store = store or get_store()
    policies = doc_store.list_policy_registry(
        request.tenant_id,
        kind=request.kind,
        index_status=request.index_status,
    )
    return ListPolicyRegistryResponse(tenant_id=request.tenant_id, policies=policies)
