"""Emit live agent status events into LangGraph custom streams."""

from __future__ import annotations


def _search_payload(query: str) -> dict[str, str]:
    lowered = (query or "").lower()
    if "indiankanoon" in lowered:
        label = "Querying Indian Kanoon"
    elif "indiacode" in lowered:
        label = "Querying India Code"
    else:
        label = "Querying case law database"
    return {"status": "searching", "label": label, "query": query}


def _crawl_payload(url: str) -> dict[str, str]:
    lowered = (url or "").lower()
    if "indiankanoon" in lowered:
        label = "Reading judgment"
    elif "sci.gov.in" in lowered or "supremecourt" in lowered:
        label = "Reading court order"
    else:
        label = "Reading source"
    return {"status": "crawling", "label": label, "url": url}


def emit_agent_status(payload: dict[str, str]) -> None:
    """Push a status dict to the active LangGraph custom stream, if any."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        if writer:
            writer(payload)
    except Exception:
        return


def emit_search_status(query: str) -> None:
    emit_agent_status(_search_payload(query))


def emit_crawl_status(url: str) -> None:
    if url:
        emit_agent_status(_crawl_payload(url))
