"""Temporary Java sync substitute — calls document-mcp like a Java worker would."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestSectionInput, ListSectionsRequest
from document_core.schemas.registry import (
    DeletePolicyRequest,
    RegisterContractRequest,
    RegisterPolicyRequest,
)
from review_agent.clients.document_client import DocumentMCPClient

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sections(raw: list[dict[str, Any]]) -> list[IngestSectionInput]:
    return [IngestSectionInput.model_validate(item) for item in raw]


class JavaSyncStub:
    """Mimics Java background sync: register → ingest with sections[]."""

    def __init__(self, client: DocumentMCPClient, *, tenant_id: str) -> None:
        self.client = client
        self.tenant_id = tenant_id

    async def health_ok(self) -> dict[str, Any]:
        return await self.client.health()

    async def sync_contract_from_data(self, data: dict[str, Any]) -> dict[str, Any]:
        tenant = data.get("tenant_id", self.tenant_id)
        contract_ref = data["contract_ref"]
        sections_raw = data.get("sections") or []
        if not sections_raw:
            raise ValueError("contract must have at least one section with text")
        source = data.get("source") or (data.get("metadata") or {}).get("source") or "dev-ui-custom"

        record = await self.client.register_contract(
            RegisterContractRequest(
                tenant_id=tenant,
                contract_ref=contract_ref,
                title=data["title"],
                contract_type=data.get("contract_type"),
                source=source,
                metadata=data.get("metadata", {}),
            )
        )

        meta = {**data.get("metadata", {}), "contract_ref": contract_ref, "source": source}
        result = await self.client.ingest_document(
            IngestRequest(
                tenant_id=tenant,
                document_id=record.document_id,
                title=data["title"],
                kind=DocumentKind.CONTRACT,
                sections=_sections(sections_raw),
                metadata=meta,
            )
        )
        return {
            "kind": "contract",
            "contract_ref": contract_ref,
            "document_id": str(record.document_id),
            "index_status_after": "indexed",
            "parent_count": result.parent_count,
            "structure_confidence": result.structure_confidence.value,
            "warnings": result.warnings,
        }

    async def sync_policy_from_data(self, data: dict[str, Any]) -> dict[str, Any]:
        tenant = data.get("tenant_id", self.tenant_id)
        policy_ref = data["policy_ref"]
        categories = data.get("categories") or data.get("metadata", {}).get("categories") or []
        source = data.get("source") or (data.get("metadata") or {}).get("source") or "dev-ui-custom"

        record = await self.client.register_policy(
            RegisterPolicyRequest(
                tenant_id=tenant,
                policy_ref=policy_ref,
                title=data["title"],
                policy_type=data.get("policy_type"),
                applies_to_contract_types=data.get("applies_to_contract_types", []),
                source=source,
                metadata=data.get("metadata", {}),
            )
        )

        meta = {**data.get("metadata", {}), "policy_ref": policy_ref, "source": source}
        if categories:
            meta["categories"] = categories
        guidance = (data.get("review_guidance") or data.get("metadata", {}).get("review_guidance") or "").strip()
        if guidance:
            meta["review_guidance"] = guidance
            meta["preferred_position"] = data.get("preferred_position") or guidance

        result = await self.client.index_policy(
            IngestRequest(
                tenant_id=tenant,
                document_id=record.document_id,
                title=data["title"],
                kind=DocumentKind.POLICY,
                sections=_sections(data["sections"]),
                categories=categories,
                policy_type=data.get("policy_type"),
                applies_to_contract_types=data.get("applies_to_contract_types", []),
                metadata=meta,
            )
        )
        return {
            "kind": "policy",
            "policy_ref": policy_ref,
            "document_id": str(record.document_id),
            "parent_count": result.parent_count,
            "structure_confidence": result.structure_confidence.value,
            "categories": categories,
            "warnings": result.warnings,
        }

    async def sync_custom(
        self,
        *,
        contract: dict[str, Any],
        policies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contract_result = await self.sync_contract_from_data(contract)
        policy_results: list[dict[str, Any]] = []
        for policy in policies:
            policy_results.append(await self.sync_policy_from_data(policy))
        verify = await self.verify_contract_indexed(contract_result["document_id"])
        return {
            "contract": contract_result,
            "policies": policy_results,
            "verify": verify,
        }

    async def sync_contract_from_fixture(self, fixture_path: Path) -> dict[str, Any]:
        data = load_json(fixture_path)
        tenant = data.get("tenant_id", self.tenant_id)
        data.setdefault("tenant_id", tenant)
        return await self.sync_contract_from_data(data)

    async def sync_policy_from_fixture(self, fixture_path: Path) -> dict[str, Any]:
        data = load_json(fixture_path)
        tenant = data.get("tenant_id", self.tenant_id)
        data.setdefault("tenant_id", tenant)
        return await self.sync_policy_from_data(data)

    async def sync_all_fixtures(self) -> dict[str, Any]:
        contract = await self.sync_contract_from_fixture(FIXTURES_DIR / "nda_contract.json")
        policies: list[dict[str, Any]] = []
        for path in sorted((FIXTURES_DIR / "policies").glob("*.json")):
            policies.append(await self.sync_policy_from_fixture(path))
        return {"contract": contract, "policies": policies}

    async def verify_contract_indexed(self, document_id: str | UUID) -> dict[str, Any]:
        sections = await self.client.list_sections(
            ListSectionsRequest(
                tenant_id=self.tenant_id,
                document_id=UUID(str(document_id)),
                kind=DocumentKind.CONTRACT,
            )
        )
        return {
            "document_id": str(document_id),
            "section_count": len(sections),
            "section_ids": [s.section_id for s in sections],
        }

    async def tombstone_policy(self, policy_ref: str) -> dict[str, Any]:
        result = await self.client.delete_policy(self.tenant_id, policy_ref)
        return {
            "policy_ref": result.policy_ref,
            "document_id": str(result.document_id),
            "index_status": result.index_status,
        }

    async def tombstone_smoke(self, policy_ref: str = "playbook-indemnification-standard") -> dict[str, Any]:
        """Delete policy then confirm search no longer returns it (chunks remain — soft tombstone)."""
        from document_core.schemas.chunk import SearchRequest

        tombstone = await self.tombstone_policy(policy_ref)
        hits = await self.client.search_policy(
            SearchRequest(
                tenant_id=self.tenant_id,
                query="indemnify gross negligence",
                kind=DocumentKind.POLICY,
                top_k=5,
            )
        )
        hit_refs = {
            (h.parent_chunk.metadata or {}).get("policy_ref")
            for h in hits
        }
        return {
            **tombstone,
            "search_hit_count": len(hits),
            "deleted_policy_in_hits": policy_ref in hit_refs,
        }
