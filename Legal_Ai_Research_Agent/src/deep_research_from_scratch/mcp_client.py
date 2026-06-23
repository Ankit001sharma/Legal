"""HTTP client for the Legal ai Retrieval MCP server."""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

import httpx

from deep_research_from_scratch.config import config

SearchType = Literal["web", "internal", "all"]
SearchDepth = Literal["normal", "deep"]
SourceType = Literal["web", "internal"]
CitationDirection = Literal["incoming", "outgoing", "both"]


class MCPClientError(Exception):
    """Raised when a retrieval MCP tool call fails after retries."""


class BaseMCPClient(ABC):
    """Base HTTP client for MCP tool servers.

    Creates a fresh ``httpx.AsyncClient`` per request by default so sync->async
    bridges (each in its own short-lived event loop) do not share a broken pool.
    """

    server_name: str = "mcp"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
        auth_token_getter: Any | None = None,
    ) -> None:
        resolved_url = base_url or os.environ.get(
            "RETRIEVAL_SERVER_URL", config.RETRIEVAL_SERVER_URL
        )
        self.base_url = resolved_url.rstrip("/")
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else config.RETRIEVAL_TIMEOUT_SECONDS
        )
        self.max_retries = max_retries if max_retries is not None else config.RETRIEVAL_MAX_RETRIES
        self._injected_client = http_client
        self._auth_token_getter = auth_token_getter

    @asynccontextmanager
    async def _acquire_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._injected_client is not None:
            yield self._injected_client
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
            yield client

    async def close(self) -> None:
        """No-op unless a subclass owns the injected HTTP client."""
        return None

    def _request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        getter = self._auth_token_getter
        if getter is None:
            from deep_research_from_scratch.retrieval_bridge import get_auth_token

            getter = get_auth_token
        token = getter()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _post(self, tool_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{tool_path}"
        tool_name = tool_path.rsplit("/", 1)[-1]
        last_error: Exception | None = None
        headers = self._request_headers()

        for attempt in range(1, self.max_retries + 1):
            started = time.perf_counter()
            try:
                async with self._acquire_client() as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                latency_ms = (time.perf_counter() - started) * 1000
                self._emit_tool_called(
                    tool_name=tool_name,
                    url=url,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    success=True,
                )
                return data
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - started) * 1000
                last_error = exc
                self._emit_tool_called(
                    tool_name=tool_name,
                    url=url,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    success=False,
                    error=str(exc),
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))

        self._emit_failure(tool_name=tool_name, error=str(last_error))
        raise MCPClientError(
            f"{self.server_name} tool '{tool_name}' failed after "
            f"{self.max_retries} attempts: {last_error}"
        ) from last_error

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
        return None

    def _emit_failure(self, *, tool_name: str, error: str) -> None:
        return None

    async def health(self) -> dict[str, Any]:
        async with self._acquire_client() as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()


class RetrievalMCPClient(BaseMCPClient):
    """Client for the retrieval server's /tools/* HTTP endpoints."""

    server_name = "retrieval-mcp"

    async def search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        jurisdiction: str = "India",
        max_results: int = 10,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
        search_depth: SearchDepth = "normal",
    ) -> list[dict[str, Any]]:
        """Unified keyword search across web and internal legal sources."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "jurisdiction": jurisdiction,
            "max_results": max_results,
            "search_depth": search_depth,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if filters:
            payload["filters"] = filters

        data = await self._post("/tools/search", payload)
        return list(data.get("results", []))

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch and extract clean text from a web URL via the MCP server."""
        return await self.fetch_and_extract(url, "web")

    async def fetch_and_extract(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        extract_sections: list[str] | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch and extract a full document."""
        payload: dict[str, Any] = {
            "source_id": source_id,
            "source_type": source_type,
        }
        if extract_sections:
            payload["extract_sections"] = extract_sections
        if tenant_id:
            payload["tenant_id"] = tenant_id
        return await self._post("/tools/fetch_and_extract", payload)

    async def save_memory(
        self, title: str, content: str, hook: str = ""
    ) -> dict[str, Any]:
        """Persist a durable legal fact to long-term memory on the MCP server."""
        payload = {"title": title, "content": content, "hook": hook}
        return await self._post("/tools/memory/save", payload)

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        """Search long-term memory on the MCP server for matching saved facts."""
        data = await self._post("/tools/memory/search", {"query": query})
        return list(data.get("results", []))

    async def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 10,
        threshold: float = 0.7,
        search_type: SearchType = "all",
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search over indexed legal documents."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "top_k": top_k,
            "threshold": threshold,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        data = await self._post("/tools/semantic_search", payload)
        if data.get("stub"):
            return []
        results = data.get("results", [])
        normalized: list[dict[str, Any]] = []
        for hit in results:
            if not isinstance(hit, dict):
                continue
            url = hit.get("url") or hit.get("source_id") or ""
            normalized.append(
                {
                    "title": hit.get("title", "Untitled"),
                    "url": url if str(url).startswith("http") else "",
                    "text_snippet": hit.get("text_snippet", ""),
                    "similarity_score": hit.get("similarity_score", 0.0),
                    "metadata": {
                        **(hit.get("metadata") or {}),
                        "backend": "semantic",
                    },
                }
            )
        return normalized

    async def citation_graph(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        depth: int = 1,
        direction: CitationDirection = "both",
    ) -> dict[str, Any]:
        """Retrieve a citation graph for a legal source."""
        payload = {
            "source_id": source_id,
            "source_type": source_type,
            "depth": depth,
            "direction": direction,
        }
        return await self._post("/tools/citation_graph", payload)


_default_client: RetrievalMCPClient | None = None


def get_retrieval_client() -> RetrievalMCPClient:
    """Return the process-wide retrieval MCP client."""
    global _default_client  # noqa: PLW0603
    if _default_client is None:
        _default_client = RetrievalMCPClient()
    return _default_client
