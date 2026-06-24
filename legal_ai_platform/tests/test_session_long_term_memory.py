"""Integration tests for Phase 3 platform long-term memory."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse, PolicyInput
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry
from legal_ai_platform.session import SessionFileStore, SessionService
from legal_ai_platform.session.memory_bridge import MemoryBridge


class _RecordingMemoryClient:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str]] = []
        self.store: list[dict[str, str]] = []

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        hits = []
        for item in self.store:
            if any(term in item["content"].lower() for term in query.lower().split()):
                hits.append({"name": item["name"], "content": item["content"]})
        return hits

    async def save_memory(
        self, title: str, content: str, hook: str = ""
    ) -> dict[str, Any]:
        self.saved.append((title, content, hook))
        self.store.append({"name": f"{len(self.store)}.md", "content": content})
        return {"message": "saved"}


class _StubReviewAgent(BaseAgent):
    agent_type = "review"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        session = request.context.get("session", {})
        snippets = session.get("memory_snippets", "")
        platform_owns = session.get("platform_owns_long_term_memory", False)
        return AgentResponse(
            agent="review",
            task_type="review",
            output="review complete",
            artifacts={
                "report": {
                    "contract_title": "MSA",
                    "findings": [
                        {
                            "dimension_label": "Liability",
                            "status": "NON_COMPLIANT",
                            "severity": "critical",
                            "rationale": "Mismatch",
                        }
                    ],
                    "structure_confidence": "high",
                },
                "memory_context": snippets,
                "platform_owns": platform_owns,
            },
            success=True,
            thread_id=request.thread_id,
        )


@pytest.fixture
def memory_client() -> _RecordingMemoryClient:
    return _RecordingMemoryClient()


@pytest.fixture
def orchestrator(memory_client: _RecordingMemoryClient) -> QueryOrchestrator:
    tmp = Path(tempfile.mkdtemp())
    bridge = MemoryBridge(memory_client)
    session_service = SessionService(SessionFileStore(tmp), memory_bridge=bridge)
    registry = AgentRegistry()
    registry.register("review", _StubReviewAgent())
    return QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=HookRegistry(),
        session_service=session_service,
    )


@pytest.mark.asyncio
async def test_prefetch_injects_memory_snippets(
    orchestrator: QueryOrchestrator,
    memory_client: _RecordingMemoryClient,
):
    memory_client.store.append(
        {
            "name": "old.md",
            "content": "prior review liability cap twelve months for tenant demo",
        }
    )
    response = await orchestrator.handle(
        AgentRequest(
            query="review liability cap",
            task_type="review",
            tenant_id="demo",
            contract_text="Contract text",
            policies=[PolicyInput(title="P", text="Policy body")],
        )
    )
    assert response.success
    assert response.artifacts.get("platform_owns") is True
    assert "prior review" in (response.artifacts.get("memory_context") or "")


@pytest.mark.asyncio
async def test_post_turn_saves_review_to_mcp(
    orchestrator: QueryOrchestrator,
    memory_client: _RecordingMemoryClient,
):
    response = await orchestrator.handle(
        AgentRequest(
            query="review",
            task_type="review",
            tenant_id="demo",
            contract_text="Contract",
            policies=[PolicyInput(title="P", text="Policy")],
        )
    )
    assert response.success
    assert response.artifacts.get("memory_saved") is True
    assert len(memory_client.saved) == 1
    _, _, hook = memory_client.saved[0]
    assert "[review][demo]" in hook
    assert response.thread_id in hook


@pytest.mark.asyncio
async def test_cross_session_search_finds_prior_review(
    orchestrator: QueryOrchestrator,
    memory_client: _RecordingMemoryClient,
):
    r1 = await orchestrator.handle(
        AgentRequest(
            query="review",
            task_type="review",
            tenant_id="demo",
            contract_text="Contract",
            policies=[PolicyInput(title="P", text="Policy")],
        )
    )
    assert r1.artifacts.get("memory_saved") is True

    r2 = await orchestrator.handle(
        AgentRequest(
            query="what did we find on liability",
            task_type="review",
            tenant_id="demo",
            contract_text="Contract",
            policies=[PolicyInput(title="P", text="Policy")],
        )
    )
    assert "Liability" in (r2.artifacts.get("memory_context") or "")
