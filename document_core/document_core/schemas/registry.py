"""Policy registry schemas (metadata catalog separate from chunk index)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterPolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    document_id: UUID | None = None
    policy_type: str | None = None
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterContractRequest(BaseModel):
    tenant_id: str
    contract_ref: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    document_id: UUID | None = None
    contract_type: str | None = None
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeletePolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)


class DeletePolicyResult(BaseModel):
    tenant_id: str
    policy_ref: str
    document_id: UUID
    index_status: Literal["deleted"]


class GetPolicyByRefRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)


class ListPolicyRegistryRequest(BaseModel):
    tenant_id: str
    kind: Literal["contract", "policy"] | None = None
    index_status: Literal["pending", "indexed", "failed", "deleted"] | None = None


class PolicyRegistryRecord(BaseModel):
    tenant_id: str
    document_id: UUID
    policy_ref: str
    title: str
    kind: Literal["contract", "policy"] = "policy"
    policy_type: str | None = None
    index_status: Literal["pending", "indexed", "failed", "deleted"]
    content_hash: str | None = None
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)
    indexed_at: datetime | None = None


class ListPolicyRegistryResponse(BaseModel):
    tenant_id: str
    policies: list[PolicyRegistryRecord]
