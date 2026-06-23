"""Emit Claude-style step progress events into LangGraph custom streams."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

_ACTIVE_GROUP_ID: str | None = None
_SEARCH_FETCH_COUNT = 0
_SEARCH_QUERY_COUNT = 0

_PROGRESS_EVENTS = frozenset({"group_start", "sub_step", "group_end", "done"})


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _shorten_url(url: str, max_len: int = 50) -> str:
    display = (url or "").replace("https://", "").replace("http://", "")
    if len(display) > max_len:
        return display[:max_len] + "..."
    return display


def emit_event(payload: dict[str, Any]) -> None:
    """Push a progress event to the active LangGraph custom stream, if any."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        if writer:
            writer(payload)
    except Exception:
        return


def group_start(group_id: str, title: str, icon: str = "search") -> None:
    """Open a new collapsible step group."""
    global _ACTIVE_GROUP_ID, _SEARCH_FETCH_COUNT, _SEARCH_QUERY_COUNT
    _ACTIVE_GROUP_ID = group_id
    if group_id.startswith("search"):
        _SEARCH_FETCH_COUNT = 0
        _SEARCH_QUERY_COUNT = 0
    emit_event(
        {
            "event": "group_start",
            "group_id": group_id,
            "group_title": title,
            "group_icon": icon,
            "timestamp_ms": _timestamp_ms(),
        }
    )


def group_end(group_id: str, summary: str) -> None:
    """Close a step group with a collapsed summary label."""
    global _ACTIVE_GROUP_ID
    if _ACTIVE_GROUP_ID == group_id:
        _ACTIVE_GROUP_ID = None
    emit_event(
        {
            "event": "group_end",
            "group_id": group_id,
            "group_summary": summary,
            "timestamp_ms": _timestamp_ms(),
        }
    )


def sub_step(
    group_id: str,
    *,
    icon: str,
    text: str,
    url: str | None = None,
) -> None:
    """Emit one line inside an open group."""
    payload: dict[str, Any] = {
        "event": "sub_step",
        "group_id": group_id,
        "sub_icon": icon,
        "sub_text": text,
        "timestamp_ms": _timestamp_ms(),
    }
    if url:
        payload["sub_url"] = url
    emit_event(payload)


def emit_think_step(group_id: str, text: str) -> None:
    sub_step(group_id, icon="think", text=text)


def _ensure_search_group() -> None:
    if _ACTIVE_GROUP_ID is None or not _ACTIVE_GROUP_ID.startswith("search"):
        group_start("search_web", "Searching the web", "search")


def emit_search_status(query: str) -> None:
    """Record a search query as a sub-step."""
    global _SEARCH_QUERY_COUNT
    _ensure_search_group()
    _SEARCH_QUERY_COUNT += 1
    q = (query or "").strip()
    sub_step(
        _ACTIVE_GROUP_ID or "search_web",
        icon="search",
        text=f'"{q}"' if q else "Running search",
    )


def emit_crawl_status(url: str) -> None:
    """Record a page fetch as a sub-step."""
    global _SEARCH_FETCH_COUNT
    if not url:
        return
    _ensure_search_group()
    _SEARCH_FETCH_COUNT += 1
    display = _shorten_url(url)
    sub_step(
        _ACTIVE_GROUP_ID or "search_web",
        icon="page",
        text=f"Reading {display}",
        url=url,
    )


def end_search_group() -> None:
    """Close the active search group with a source-count summary."""
    global _ACTIVE_GROUP_ID, _SEARCH_FETCH_COUNT, _SEARCH_QUERY_COUNT
    if not _ACTIVE_GROUP_ID or not _ACTIVE_GROUP_ID.startswith("search"):
        return
    gid = _ACTIVE_GROUP_ID
    if _SEARCH_FETCH_COUNT > 0:
        summary = (
            f"Read {_SEARCH_FETCH_COUNT} source"
            f"{'' if _SEARCH_FETCH_COUNT == 1 else 's'}"
        )
    elif _SEARCH_QUERY_COUNT > 0:
        summary = (
            f"Ran {_SEARCH_QUERY_COUNT} search"
            f"{'' if _SEARCH_QUERY_COUNT == 1 else 'es'}"
        )
    else:
        summary = "Search complete"
    group_end(gid, summary)


def emit_done() -> None:
    emit_event({"event": "done", "timestamp_ms": _timestamp_ms()})


def reset_progress_state() -> None:
    """Reset module state (useful in tests)."""
    global _ACTIVE_GROUP_ID, _SEARCH_FETCH_COUNT, _SEARCH_QUERY_COUNT
    _ACTIVE_GROUP_ID = None
    _SEARCH_FETCH_COUNT = 0
    _SEARCH_QUERY_COUNT = 0


def _progress_payload_from_stream_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a progress dict from a LangGraph v2 ``astream_events`` item."""
    chunk = (event.get("data") or {}).get("chunk")
    payload: Any = None
    if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "custom":
        payload = chunk[1]
    elif isinstance(chunk, dict) and chunk.get("event") in _PROGRESS_EVENTS:
        payload = chunk
    elif event.get("event") == "on_custom_event":
        payload = event.get("data")
    if isinstance(payload, dict) and payload.get("event") in _PROGRESS_EVENTS:
        return payload
    return None


async def astream_with_progress(
    graph: Any,
    input_state: dict[str, Any],
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Run *graph* and forward nested custom progress events to the parent stream.

    Compiled subgraph nodes do not bubble ``get_stream_writer()`` events to the
    top-level graph. This helper re-emits them while the graph runs so the UI
    receives live search / analyze steps (same behaviour as normal research).
    """
    final_output: dict[str, Any] | None = None

    async for event in graph.astream_events(
        input_state,
        config=config,
        version="v2",
        stream_mode=["updates", "custom"],
        include_subgraphs=True,
    ):
        payload = _progress_payload_from_stream_event(event)
        if payload is not None:
            emit_event(payload)

        if event.get("event") == "on_chain_end" and event.get("name") == "LangGraph":
            output = (event.get("data") or {}).get("output")
            if isinstance(output, dict):
                final_output = output

    return final_output if final_output is not None else {}
