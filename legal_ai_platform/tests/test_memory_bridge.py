"""Tests for platform MemoryBridge (Phase 3 long-term memory)."""

from __future__ import annotations

from typing import Any

import pytest

from legal_ai_platform.session.memory_bridge import (
    MemoryBridge,
    build_memory_hook,
    build_review_memory_payload,
    format_memory_hits,
)
from legal_ai_platform.session.models import MatterSnapshot


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str]] = []
        self.search_results: dict[str, list[dict[str, Any]]] = {}

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        return self.search_results.get(query, [])

    async def save_memory(
        self, title: str, content: str, hook: str = ""
    ) -> dict[str, Any]:
        self.saved.append((title, content, hook))
        return {"message": "saved", "filename": "test.md"}


def test_build_memory_hook_tags():
    hook = build_memory_hook(
        agent="review",
        tenant_id="acme",
        thread_id="thread-1",
        detail="2 findings (1 critical)",
    )
    assert hook == "[review][acme][thread-1] 2 findings (1 critical)"


def test_format_memory_hits_empty():
    assert format_memory_hits([]) == ""


def test_build_review_memory_payload_skips_empty_findings():
    assert (
        build_review_memory_payload(
            {"findings": []},
            tenant_id="t",
            thread_id="th",
            contract_title="MSA",
        )
        is None
    )


def test_build_review_memory_payload_includes_findings():
    report = {
        "findings": [
            {
                "dimension_label": "Liability",
                "status": "NON_COMPLIANT",
                "severity": "critical",
                "rationale": "Cap too low",
                "contract_quote": "12.2 cap",
            }
        ],
        "structure_confidence": "high",
    }
    title, body, hook = build_review_memory_payload(
        report,
        tenant_id="acme",
        thread_id="th-99",
        contract_title="MSA",
    )
    assert title == "Review: MSA [acme]"
    assert "Liability" in body
    assert "[review][acme][th-99]" in hook


@pytest.mark.asyncio
async def test_memory_bridge_search_dedupes():
    client = _FakeMemoryClient()
    client.search_results = {
        "review MSA": [{"name": "a.md", "content": "prior review"}],
        "compliance tenant acme": [{"name": "a.md", "content": "prior review"}],
    }
    bridge = MemoryBridge(client, max_hits=5)
    snippets, hits = await bridge.search(
        query="review MSA",
        tenant_id="acme",
        task_type="review",
        matter=MatterSnapshot(contract_title="MSA"),
    )
    assert "prior review" in snippets
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_memory_bridge_save_review_report():
    client = _FakeMemoryClient()
    bridge = MemoryBridge(client)
    report = {
        "findings": [{"dimension_label": "X", "status": "COMPLIANT", "severity": "info"}],
        "structure_confidence": "high",
    }
    result = await bridge.save_review_report(
        report,
        tenant_id="acme",
        thread_id="th-1",
        contract_title="NDA",
    )
    assert result is not None
    assert result["memory_saved"] is True
    assert len(client.saved) == 1
    _, _, hook = client.saved[0]
    assert "[review][acme][th-1]" in hook
