"""LangGraph nodes for retrieval MCP memory (same store as research agent)."""

from __future__ import annotations

from typing import Any

from review_agent.clients.memory_client import MemoryMCPClient
from review_agent.services.memory import build_memory_search_queries, format_memory_hits
from review_agent.state.review_state import ReviewState


async def load_memory_node(
    state: ReviewState,
    memory_client: MemoryMCPClient | None,
) -> dict[str, Any]:
    """Search long-term memory before review (shared MEMORY.md store with research)."""
    if memory_client is None:
        return {}

    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    queries = build_memory_search_queries(
        tenant_id=state["tenant_id"],
        contract_title=state.get("contract_title") or "Contract",
        contract_type=state.get("contract_type"),
    )

    for query in queries:
        try:
            batch = await memory_client.search_memory(query)
        except Exception:  # noqa: BLE001
            continue
        for item in batch:
            key = item.get("name") or item.get("content", "")[:80]
            if key in seen:
                continue
            seen.add(key)
            hits.append(item)

    context = format_memory_hits(hits[:5])
    warnings: list[str] = []
    if context:
        warnings.append(f"loaded {len(hits)} prior memory hit(s) from retrieval-mcp")
    return {"memory_context": context, "memory_hits": hits, "warnings": warnings}


async def save_review_memory_node(
    state: ReviewState,
    memory_client: MemoryMCPClient | None,
) -> dict[str, Any]:
    """Persist completed review summary to long-term memory for future sessions."""
    if memory_client is None:
        return {}

    report = state.get("report")
    if report is None:
        return {}

    tenant = state["tenant_id"]
    title = state.get("contract_title") or report.contract_title
    finding_count = len(report.findings)
    critical = sum(1 for f in report.findings if f.severity.value == "critical")

    memory_title = f"Review: {title} [{tenant}]"
    hook = (
        f"{finding_count} findings ({critical} critical); "
        f"structure={report.structure_confidence}"
    )
    body = report.summary_markdown
    if state.get("memory_context"):
        body = (
            f"## Session memory used\n{state['memory_context'][:2000]}\n\n"
            f"## Report\n{body}"
        )

    try:
        result = await memory_client.save_memory(memory_title, body, hook)
        return {
            "memory_saved": True,
            "memory_save_message": result.get("message", "saved"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "memory_saved": False,
            "warnings": [f"memory save failed: {exc}"],
        }
