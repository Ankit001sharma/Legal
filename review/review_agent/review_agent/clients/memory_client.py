"""Protocol for retrieval MCP long-term memory tools (shared with research agent)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryMCPClient(Protocol):
    """Async client for /tools/memory/save and /tools/memory/search on retrieval-mcp."""

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        """Return memory hits: [{name, content}, ...]."""
        ...

    async def save_memory(
        self,
        title: str,
        content: str,
        hook: str = "",
    ) -> dict[str, Any]:
        """Persist a memory entry; returns server response dict."""
        ...
