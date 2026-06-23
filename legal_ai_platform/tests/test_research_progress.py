"""Tests for research agent SSE progress extraction."""

from __future__ import annotations

from legal_ai_platform.agents.research.research_agent import _extract_progress_payload


def test_extract_progress_from_custom_chain_stream_tuple():
    event = {
        "event": "on_chain_stream",
        "data": {
            "chunk": (
                "custom",
                {
                    "event": "group_start",
                    "group_id": "search_web",
                    "group_title": "Searching the web",
                    "timestamp_ms": 123,
                },
            )
        },
    }
    payload = _extract_progress_payload(event)
    assert payload is not None
    assert payload["event"] == "group_start"
    assert payload["group_id"] == "search_web"


def test_extract_progress_from_custom_chain_stream_dict():
    event = {
        "event": "on_chain_stream",
        "data": {
            "chunk": {
                "event": "sub_step",
                "group_id": "search_web",
                "sub_icon": "search",
                "sub_text": '"site:indiacode.nic.in"',
            }
        },
    }
    payload = _extract_progress_payload(event)
    assert payload is not None
    assert payload["event"] == "sub_step"


def test_extract_progress_from_on_custom_event():
    event = {
        "event": "on_custom_event",
        "data": {
            "event": "group_end",
            "group_id": "search_web",
            "group_summary": "Read 2 sources",
        },
    }
    payload = _extract_progress_payload(event)
    assert payload is not None
    assert payload["group_summary"] == "Read 2 sources"


def test_ignores_non_progress_chain_stream():
    event = {
        "event": "on_chain_stream",
        "data": {"chunk": ("updates", {"normal_researcher": {"notes": []}})},
    }
    assert _extract_progress_payload(event) is None
