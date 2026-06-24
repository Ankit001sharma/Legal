"""Async wrappers for policy registry operations (offload sync DB from event loop)."""

from __future__ import annotations

import asyncio

from document_core.schemas.registry import (
    DeletePolicyRequest,
    DeletePolicyResult,
    ListPolicyRegistryRequest,
    ListPolicyRegistryResponse,
    PolicyRegistryRecord,
    RegisterContractRequest,
    RegisterPolicyRequest,
)
from document_core.services.registry import (
    delete_policy,
    get_contract_by_ref,
    get_policy_by_ref,
    list_policy_registry,
    register_contract,
    register_policy,
)


async def register_policy_async(request: RegisterPolicyRequest) -> PolicyRegistryRecord:
    return await asyncio.to_thread(register_policy, request)


async def register_contract_async(request: RegisterContractRequest) -> PolicyRegistryRecord:
    return await asyncio.to_thread(register_contract, request)


async def get_policy_by_ref_async(
    tenant_id: str,
    policy_ref: str,
) -> PolicyRegistryRecord | None:
    return await asyncio.to_thread(get_policy_by_ref, tenant_id, policy_ref)


async def get_contract_by_ref_async(
    tenant_id: str,
    contract_ref: str,
) -> PolicyRegistryRecord | None:
    return await asyncio.to_thread(get_contract_by_ref, tenant_id, contract_ref)


async def delete_policy_async(request: DeletePolicyRequest) -> DeletePolicyResult:
    return await asyncio.to_thread(delete_policy, request)


async def list_policy_registry_async(
    request: ListPolicyRegistryRequest,
) -> ListPolicyRegistryResponse:
    return await asyncio.to_thread(list_policy_registry, request)
