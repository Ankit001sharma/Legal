"""Tests for research ↔ platform session alignment (Phase 4)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry
from legal_ai_platform.session import SessionFileStore, SessionService


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        session = request.context.get("session", {})
        return AgentResponse(
            agent="research",
            task_type="research",
            output="ok",
            artifacts={
                "platform_owns_session": session.get("platform_owns_session"),
                "transcript_turns": len(session.get("transcript_recent") or []),
                "has_summary": bool(session.get("summary")),
            },
            success=True,
            thread_id=request.thread_id,
        )


@pytest.fixture
def orchestrator() -> QueryOrchestrator:
    tmp = Path(tempfile.mkdtemp())
    session_service = SessionService(
        SessionFileStore(tmp),
        platform_owns_session=True,
    )
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    return QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=HookRegistry(),
        session_service=session_service,
    )


@pytest.mark.asyncio
async def test_research_receives_platform_session_context(orchestrator: QueryOrchestrator):
    r1 = await orchestrator.handle(
        AgentRequest(query="What is IPC 420?", tenant_id="demo")
    )
    assert r1.success
    assert r1.artifacts.get("platform_owns_session") is True

    thread_id = r1.thread_id
    r2 = await orchestrator.handle(
        AgentRequest(query="Tell me more", tenant_id="demo", thread_id=thread_id)
    )
    assert r2.artifacts.get("transcript_turns", 0) >= 2
