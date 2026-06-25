"""Tests for review agent memory integration via retrieval MCP."""

from __future__ import annotations

from typing import Any

import pytest

from review_agent.graph.memory_nodes import load_memory_node, save_review_memory_node
from review_agent.state.review_state import ReviewState
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, ReviewReport
from uuid import uuid4


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.saved: list[dict[str, str]] = []
        self.search_results: list[dict[str, Any]] = [
            {"name": "prior_msa.md", "content": "Prior review flagged liability cap."}
        ]

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        return self.search_results

    async def save_memory(self, title: str, content: str, hook: str = "") -> dict[str, Any]:
        self.saved.append({"title": title, "content": content, "hook": hook})
        return {"message": "saved", "filename": "test.md"}


@pytest.mark.asyncio
async def test_load_memory_node():
    state: ReviewState = {
        "tenant_id": "demo",
        "contract_title": "Vendor MSA",
        "contract_type": "msa",
    }
    update = await load_memory_node(state, _FakeMemoryClient())
    assert "Prior review flagged" in update["memory_context"]
    assert update["memory_hits"]


@pytest.mark.asyncio
async def test_save_review_memory_node():
    report = ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid4(),
        contract_title="MSA",
        findings=[
            ComplianceFinding(
                finding_id="1",
                dimension_id="liability",
                dimension_label="Liability",
                status=ComplianceStatus.COMPLIANT,
            )
        ],
        summary_markdown="# Report\nok",
    )
    state: ReviewState = {
        "tenant_id": "demo",
        "contract_title": "MSA",
        "report": report,
    }
    client = _FakeMemoryClient()
    update = await save_review_memory_node(state, client)
    assert update["memory_saved"] is True
    assert client.saved[0]["title"].startswith("Review:")
