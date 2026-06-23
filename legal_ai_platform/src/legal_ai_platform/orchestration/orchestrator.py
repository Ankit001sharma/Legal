"""Query orchestrator — classifies, routes, and invokes agents."""

from __future__ import annotations

import logging
import time
from typing import NoReturn

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.events import AgentSelected, Failure, Latency, QueryReceived
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.registry import AgentRegistry

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """Raised when no agent is registered for the classified task type."""


class QueryOrchestrator:
    """Receive user queries, classify, select agent, invoke, and return response."""

    def __init__(
        self,
        registry: AgentRegistry,
        classifier: TaskClassifier | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.classifier = classifier or TaskClassifier()
        self.hooks = hooks or HookRegistry()

    def resolve(self, request: AgentRequest) -> tuple[str, BaseAgent]:
        """Classify and select the agent for a request."""
        explicit_types = (
            [task_type.value for task_type in request.task_type]
            if request.task_type
            else None
        )

        self.hooks.emit(
            QueryReceived(query=request.query, task_type=explicit_types[0] if explicit_types else "")
        )

        task_type: str | None = None
        agent: BaseAgent | None = None

        if explicit_types:
            for candidate in explicit_types:
                agent = self.registry.get(candidate)
                if agent is not None:
                    task_type = candidate
                    break
            if agent is None:
                self._raise_agent_not_found(", ".join(explicit_types))
        else:
            task_type = self.classifier.classify(request.query)
            agent = self.registry.get(task_type)
            if agent is None:
                fallback_type = self.classifier.DEFAULT_TASK_TYPE
                agent = self.registry.get(fallback_type)
                if agent is None or task_type == fallback_type:
                    self._raise_agent_not_found(task_type)
                logger.info(
                    "No agent for classified task_type=%s; falling back to %s",
                    task_type,
                    fallback_type,
                )
                task_type = fallback_type

        assert agent is not None and task_type is not None
        self.hooks.emit(AgentSelected(task_type=task_type, agent_type=agent.agent_type))
        return task_type, agent

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """Process a user query end-to-end."""
        started = time.perf_counter()
        task_type, agent = self.resolve(request)
        logger.info(
            "orchestrator dispatch task_type=%s agent=%s session_id=%s query_len=%d",
            task_type,
            agent.agent_type,
            request.session_id,
            len(request.query),
        )

        response = await agent.execute(request)
        response.task_type = task_type

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "orchestrator completed task_type=%s success=%s awaiting_input=%s latency_ms=%.0f",
            task_type,
            response.success,
            response.awaiting_input,
            latency_ms,
        )
        self.hooks.emit(
            Latency(operation="orchestrator.handle", latency_ms=latency_ms)
        )
        return response

    def _raise_agent_not_found(self, task_type: str) -> NoReturn:
        self.hooks.emit(
            Failure(
                operation="orchestrator.handle",
                error=f"No agent registered for task_type={task_type}",
                recoverable=False,
            )
        )
        raise AgentNotFoundError(
            f"No agent registered for task_type='{task_type}'. "
            f"Available: {self.registry.list_task_types()}"
        )
