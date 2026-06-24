"""Tests for QueryOrchestrator routing."""

import pytest
from uuid import uuid4

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError, QueryOrchestrator, ReviewPayloadError
from legal_ai_platform.orchestration.registry import AgentRegistry


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="research",
            output=f"Report for: {request.query}",
        )


class _StubReviewAgent(BaseAgent):
    agent_type = "review"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="review",
            output="review ok",
            success=True,
        )


@pytest.mark.asyncio
async def test_orchestrator_routes_to_registered_agent():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=HookRegistry(),
    )
    response = await orchestrator.handle(AgentRequest(query="What is IPC 420?"))
    assert response.success is True
    assert response.agent == "research"
    assert "IPC 420" in response.output


@pytest.mark.asyncio
async def test_orchestrator_routes_review_intent_to_review_agent():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    registry.register("review", _StubReviewAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(
            query="Review this NDA",
            contract_document_id=str(uuid4()),
            policy_document_ids=[str(uuid4())],
        )
    )
    assert response.agent == "review"
    assert response.task_type == "review"


@pytest.mark.asyncio
async def test_orchestrator_raises_when_agent_not_registered():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(AgentRequest(query="Draft a legal notice for breach"))


@pytest.mark.asyncio
async def test_orchestrator_contract_alias_maps_to_review():
    registry = AgentRegistry()
    registry.register("review", _StubReviewAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(
            query="check",
            task_type="contract",
            contract_document_id=str(uuid4()),
            policy_document_ids=[str(uuid4())],
        )
    )
    assert response.agent == "review"


@pytest.mark.asyncio
async def test_orchestrator_review_validation():
    registry = AgentRegistry()
    registry.register("review", _StubReviewAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    with pytest.raises(ReviewPayloadError):
        await orchestrator.handle(AgentRequest(query="review only", task_type="review"))


@pytest.mark.asyncio
async def test_orchestrator_accepts_contract_text_indexed_review():
    registry = AgentRegistry()
    registry.register("review", _StubReviewAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(
            query="Review this NDA for compliance",
            task_type="review",
            tenant_id="demo",
            contract_text="Section 1. Confidential Information shall be protected.",
            policy_source="indexed",
        )
    )
    assert response.agent == "review"
    assert response.success is True


@pytest.mark.asyncio
async def test_orchestrator_respects_explicit_task_type():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(query="anything", task_type="research")
    )
    assert response.agent == "research"
