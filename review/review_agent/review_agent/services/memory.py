"""Format and build queries for retrieval MCP memory tools."""

from __future__ import annotations

from typing import Any


def format_memory_hits(results: list[dict[str, Any]]) -> str:
    """Turn MCP memory search hits into text for the review graph."""
    if not results:
        return ""
    parts: list[str] = []
    for hit in results:
        name = hit.get("name", "memory")
        content = hit.get("content", "")
        if content:
            parts.append(f"--- {name} ---\n{content}")
    if not parts:
        return ""
    return "Prior review / legal memories:\n\n" + "\n\n".join(parts)


def build_memory_search_queries(
    *,
    tenant_id: str,
    contract_title: str,
    contract_type: str | None,
) -> list[str]:
    """Queries to recall prior reviews and tenant-specific compliance notes."""
    queries = [
        f"review {contract_title}",
        f"compliance tenant {tenant_id}",
    ]
    if contract_type:
        queries.append(f"review {contract_type} policy compliance")
    return queries
