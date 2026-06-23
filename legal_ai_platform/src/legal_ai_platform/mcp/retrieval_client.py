"""Typed HTTP client for the Legal ai Retrieval MCP Server."""

from __future__ import annotations

import logging
from typing import Any, Literal

from deep_research_from_scratch.mcp_client import (
    CitationDirection,
    RetrievalMCPClient as _RawRetrievalMCPClient,
    SearchType,
    SourceType,
)
from legal_ai_platform.models.retrieval import (
    CitationGraphResult,
    FetchResult,
    RetrievalResult,
)
from legal_ai_platform.observability.events import Failure, ToolCalled
from legal_ai_platform.observability.hooks import HookRegistry


logger = logging.getLogger(__name__)


class RetrievalMCPClient(_RawRetrievalMCPClient):
    """Retrieval MCP client that normalizes responses to platform domain models."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        hooks: HookRegistry | None = None,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            http_client=http_client,
        )
        self.hooks = hooks or HookRegistry()
        logger.info(
            "retrieval client initialized base_url=%s timeout_s=%s max_retries=%d",
            base_url,
            timeout_seconds,
            max_retries,
        )

    def _emit_tool_called(
        self,
        *,
        tool_name: str,
        url: str,
        latency_ms: float,
        attempt: int,
        success: bool,
        error: str | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"attempt": attempt, "url": url}
        if error is not None:
            metadata["error"] = error
        self.hooks.emit(
            ToolCalled(
                tool_name=tool_name,
                server=self.server_name,
                latency_ms=latency_ms,
                success=success,
                metadata=metadata,
            )
        )

    def _emit_failure(self, *, tool_name: str, error: str) -> None:
        self.hooks.emit(
            Failure(
                operation=f"{self.server_name}.{tool_name}",
                error=error,
                recoverable=False,
            )
        )

    async def search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        jurisdiction: str = "India",
        max_results: int = 10,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
        search_depth: Literal["normal", "deep"] = "normal",
    ) -> list[RetrievalResult]:
        hits = await super().search(
            query,
            search_type=search_type,
            jurisdiction=jurisdiction,
            max_results=max_results,
            tenant_id=tenant_id,
            filters=filters,
            search_depth=search_depth,
        )
        return [RetrievalResult.from_search_hit(hit) for hit in hits]

    async def search_notifications(
        self,
        query: str,
        *,
        max_results: int = 10,
        jurisdiction: str = "India",
    ) -> list[RetrievalResult]:
        return await self.search(
            query,
            search_type="web",
            jurisdiction=jurisdiction,
            max_results=max_results,
            filters={"content_type": "notification"},
        )

    async def semantic_search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        top_k: int = 10,
        threshold: float = 0.7,
        tenant_id: str | None = None,
    ) -> list[RetrievalResult]:
        hits = await super().semantic_search(
            query,
            search_type=search_type,
            top_k=top_k,
            threshold=threshold,
            tenant_id=tenant_id,
        )
        return [RetrievalResult.from_search_hit(hit) for hit in hits]

    async def fetch_and_extract(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        extract_sections: list[str] | None = None,
        tenant_id: str | None = None,
    ) -> FetchResult:
        data = await super().fetch_and_extract(
            source_id,
            source_type,
            extract_sections=extract_sections,
            tenant_id=tenant_id,
        )
        return FetchResult(
            source_id=data.get("source_id", source_id),
            source_type=data.get("source_type", source_type),
            title=data.get("title", ""),
            full_text=data.get("full_text", ""),
            url=data.get("url", ""),
            sections=[
                {
                    "section_id": s.get("section_id", ""),
                    "title": s.get("title", ""),
                    "content": s.get("content", ""),
                }
                for s in data.get("sections", [])
            ],
            metadata=data.get("metadata") or {},
            fetch_time_ms=data.get("fetch_time_ms", 0),
        )

    async def citation_graph(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        depth: int = 1,
        direction: CitationDirection = "both",
    ) -> CitationGraphResult:
        data = await super().citation_graph(
            source_id,
            source_type,
            depth=depth,
            direction=direction,
        )
        return CitationGraphResult(
            source_id=data.get("source_id", source_id),
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            depth=data.get("depth", depth),
            direction=data.get("direction", direction),
            stub=data.get("stub", False),
            stub_reason=data.get("stub_reason"),
            graph_time_ms=data.get("graph_time_ms", 0),
        )
