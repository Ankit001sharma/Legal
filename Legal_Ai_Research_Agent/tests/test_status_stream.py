"""Tests for live progress streaming helpers."""

from __future__ import annotations

import asyncio
import time

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import deep_research_from_scratch.status_stream as status_stream
from deep_research_from_scratch.status_stream import astream_with_progress


class _State(TypedDict):
    x: int


def _slow_emit_node(state: _State) -> dict:
    writer = get_stream_writer()
    writer(
        {
            "event": "group_start",
            "group_id": "search_web",
            "group_title": "Searching the web",
            "timestamp_ms": 1,
        }
    )
    time.sleep(0.05)
    writer(
        {
            "event": "sub_step",
            "group_id": "search_web",
            "sub_icon": "search",
            "sub_text": '"live query"',
            "timestamp_ms": 2,
        }
    )
    return {"x": state.get("x", 0) + 1}


def test_astream_with_progress_forwards_custom_events():
    builder = StateGraph(_State)
    builder.add_node("work", _slow_emit_node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile()

    captured: list[dict] = []
    original_emit = status_stream.emit_event

    def capture(payload: dict) -> None:
        captured.append(payload)
        original_emit(payload)

    status_stream.emit_event = capture  # type: ignore[assignment]
    try:
        final = asyncio.run(astream_with_progress(graph, {"x": 0}))
    finally:
        status_stream.emit_event = original_emit  # type: ignore[assignment]

    events = [p["event"] for p in captured]
    assert "group_start" in events
    assert "sub_step" in events
    assert final.get("x") == 1
