"""Async registry service wrappers."""

from __future__ import annotations

import pytest

from document_core.schemas.registry import RegisterPolicyRequest
from document_core.services.registry_async import register_policy_async
from document_core.store.memory_store import reset_store, set_store
from document_core.store.pgvector_store import PgVectorDocumentStore


@pytest.mark.asyncio
async def test_register_policy_async_offloads_to_thread(store: PgVectorDocumentStore):
    set_store(store)
    try:
        record = await register_policy_async(
            RegisterPolicyRequest(
                tenant_id="t-async",
                policy_ref="policy-async-1",
                title="Async Policy",
            )
        )
        assert record.policy_ref == "policy-async-1"
        assert record.index_status == "pending"
    finally:
        reset_store()
