"""JSON salvage tests for llm_gateway (Phase 22 P8)."""

from __future__ import annotations

from review_agent.models.llm_gateway import _extract_json_payload


def test_json_salvage_extra_data():
    raw = '{"categories": ["liability"]}{"categories": ["indemnity"]}'
    payload = _extract_json_payload(raw)
    assert "items" in payload
    assert len(payload["items"]) == 2


def test_json_array_payload():
    raw = '[{"section_id": "1", "categories": ["liability"]}]'
    payload = _extract_json_payload(raw)
    assert isinstance(payload, list)
    assert payload[0]["categories"] == ["liability"]


def test_json_single_object():
    raw = '{"items": [{"section_id": "2", "categories": ["confidentiality"]}]}'
    payload = _extract_json_payload(raw)
    assert payload["items"][0]["section_id"] == "2"
