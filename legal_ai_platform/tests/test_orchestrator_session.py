"""Tests for unified session across orchestrator turns."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator, ReviewPayloadError
from legal_ai_platform.orchestration.registry import AgentRegistry
from legal_ai_platform.session import SessionFileStore, SessionService


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        session = request.context.get("session", {})
        transcript_len = len(session.get("transcript_recent", []))
        return AgentResponse(
            agent="research",
            task_type="research",
            output=f"research answer (transcript_turns={transcript_len})",
            success=True,
            thread_id=request.thread_id,
        )


class _StubReviewAgent(BaseAgent):
    agent_type = "review"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        ctx = request.effective_context()
        contract_id = ctx.get("contract_document_id", "")
        return AgentResponse(
            agent="review",
            task_type="review",
            output=f"review done: {contract_id[:8]}",
            artifacts={
                "report": {"findings": [{"id": "f1"}]},
                "contract_document_id": contract_id,
                "policy_document_ids": ctx.get("policy_document_ids") or [],
            },
            success=True,
            thread_id=request.thread_id,
        )


@pytest.fixture
def orchestrator() -> QueryOrchestrator:
    tmp = Path(tempfile.mkdtemp())
    session_service = SessionService(SessionFileStore(tmp))
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    registry.register("review", _StubReviewAgent())
    return QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=HookRegistry(),
        session_service=session_service,
    )


@pytest.mark.asyncio
async def test_multi_turn_transcript_grows(orchestrator: QueryOrchestrator):
    r1 = await orchestrator.handle(
        AgentRequest(query="What is limitation period?", tenant_id="demo")
    )
    assert r1.success
    thread_id = r1.thread_id
    assert thread_id

    r2 = await orchestrator.handle(
        AgentRequest(
            query="Tell me more",
            tenant_id="demo",
            thread_id=thread_id,
        )
    )
    assert r2.success
    assert "transcript_turns=" in r2.output


@pytest.mark.asyncio
async def test_review_then_followup_uses_matter(orchestrator: QueryOrchestrator):
    contract_id = str(uuid4())
    policy_id = str(uuid4())
    r1 = await orchestrator.handle(
        AgentRequest(
            query="review contract",
            task_type="review",
            tenant_id="demo",
            contract_document_id=contract_id,
            policy_document_ids=[policy_id],
        )
    )
    assert r1.success
    thread_id = r1.thread_id

    # Follow-up review without resending contract/policies — matter from session
    r2 = await orchestrator.handle(
        AgentRequest(
            query="review again for compliance",
            task_type="review",
            tenant_id="demo",
            thread_id=thread_id,
        )
    )
    assert r2.success
    assert "review done" in r2.output


@pytest.mark.asyncio
async def test_review_without_policies_or_matter_fails(orchestrator: QueryOrchestrator):
    with pytest.raises(ReviewPayloadError):
        await orchestrator.handle(
            AgentRequest(query="review only", task_type="review", tenant_id="demo")
        )
