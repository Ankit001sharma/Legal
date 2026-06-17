"""HTTP client for document-mcp (platform integration)."""

from __future__ import annotations

from document_core.schemas.chunk import (
    GroundingCheckRequest,
    GroundingCheckResult,
    IndexedChunk,
    IngestRequest,
    IngestResult,
    ListSectionsRequest,
    RetrievalHit,
    SearchRequest,
)
from legal_ai_platform.mcp.base_client import BaseMCPClient


class DocumentMCPClient(BaseMCPClient):
    """Typed client for document-mcp /tools/* endpoints."""

    server_name = "document-mcp"

    async def ingest_document(self, request: IngestRequest) -> IngestResult:
        data = await self._post("/tools/ingest_document", request.model_dump(mode="json"))
        return IngestResult.model_validate(data)

    async def index_policy(self, request: IngestRequest) -> IngestResult:
        data = await self._post("/tools/index_policy", request.model_dump(mode="json"))
        return IngestResult.model_validate(data)

    async def search_contract(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_contract", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def search_policy(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_policy", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def list_sections(self, request: ListSectionsRequest) -> list[IndexedChunk]:
        data = await self._post("/tools/list_sections", request.model_dump(mode="json"))
        return [IndexedChunk.model_validate(item) for item in data.get("sections", [])]

    async def verify_quote(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        data = await self._post("/tools/verify_quote", request.model_dump(mode="json"))
        return GroundingCheckResult.model_validate(data)

    async def verify_policy_quote(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        data = await self._post("/tools/verify_policy_quote", request.model_dump(mode="json"))
        return GroundingCheckResult.model_validate(data)
