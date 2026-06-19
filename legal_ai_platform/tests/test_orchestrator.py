"""Tests for QueryOrchestrator routing."""

import pytest

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.task_types import TaskType
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError, QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry

_SESSION = "frontend-session-1"


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="research",
            output=f"Report for: {request.query}",
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
    response = await orchestrator.handle(
        AgentRequest(query="What is IPC 420?", session_id=_SESSION)
    )
    assert response.success is True
    assert response.agent == "research"
    assert "IPC 420" in response.output


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_research_when_specialized_agent_missing():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(query="Review this NDA contract", session_id=_SESSION)
    )
    assert response.success is True
    assert response.agent == "research"
    assert "NDA contract" in response.output


@pytest.mark.asyncio
async def test_orchestrator_raises_when_explicit_task_type_missing():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(
            AgentRequest(
                query="Review this NDA contract",
                task_type=[TaskType.CONTRACT],
                session_id=_SESSION,
            )
        )


@pytest.mark.asyncio
async def test_orchestrator_respects_explicit_task_type():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(query="anything", task_type=[TaskType.RESEARCH], session_id=_SESSION)
    )
    assert response.agent == "research"


@pytest.mark.asyncio
async def test_orchestrator_uses_first_registered_explicit_task_type():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(
            query="anything",
            task_type=[TaskType.CONTRACT, TaskType.RESEARCH],
            session_id=_SESSION,
        )
    )
    assert response.agent == "research"
    assert response.task_type == "research"
