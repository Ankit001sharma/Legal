"""Query orchestrator — classifies, routes, and invokes agents."""

from __future__ import annotations

import logging
import time

from legal_ai_platform.config import get_settings
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.events import AgentSelected, Failure, Latency, QueryReceived
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.registry import AgentRegistry
from legal_ai_platform.session.service import SessionService

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """Raised when no agent is registered for the classified task type."""


class ReviewPayloadError(ValueError):
    """Raised when a review request is missing required fields."""


class QueryOrchestrator:
    """Receive user queries, classify, select agent, invoke, and return response."""

    def __init__(
        self,
        registry: AgentRegistry,
        classifier: TaskClassifier | None = None,
        hooks: HookRegistry | None = None,
        session_service: SessionService | None = None,
    ) -> None:
        self.registry = registry
        self.classifier = classifier or TaskClassifier()
        self.hooks = hooks or HookRegistry()
        self.session_service = session_service

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """Process a user query end-to-end with unified session memory."""
        started = time.perf_counter()
        session_svc = self.session_service

        thread_id = (
            session_svc.resolve_thread_id(request.thread_id)
            if session_svc
            else (request.thread_id or "")
        )
        tenant_id = request.tenant_id or "default"

        session = None
        if session_svc is not None:
            session = session_svc.load_or_create(thread_id, tenant_id)
            session_svc.append_user_turn(session, request.query)
            session_svc.capture_matter_from_request(session, request)

            effective_context = request.effective_context()
            task_type_preview = self.classifier.classify(
                request.query,
                request.task_type,
                effective_context,
            )
            memory_snippets, memory_hits = await session_svc.prefetch_long_term_memory(
                session, request.query, task_type_preview
            )
            agent_request = session_svc.enrich_request(
                request,
                session,
                memory_snippets=memory_snippets,
                memory_hits=memory_hits,
            )
        else:
            agent_request = request.model_copy(update={"thread_id": thread_id or request.thread_id})

        effective_context = agent_request.effective_context()
        task_type = self.classifier.classify(
            agent_request.query,
            agent_request.task_type,
            effective_context,
        )

        self.hooks.emit(
            QueryReceived(query=agent_request.query, task_type=task_type)
        )

        if task_type == "review":
            self._validate_review_payload(agent_request, effective_context)

        agent = self.registry.get(task_type)
        if agent is None:
            self._raise_agent_not_found(task_type)

        self.hooks.emit(
            AgentSelected(task_type=task_type, agent_type=agent.agent_type)
        )

        response = await agent.execute(agent_request)
        response.task_type = task_type
        response.thread_id = thread_id or response.thread_id

        if session_svc is not None and session is not None:
            session_svc.append_assistant_turn(
                session,
                content=response.output,
                agent=response.agent,
                task_type=task_type,
            )
            session_svc.capture_matter_from_response(session, response)
            session_svc.capture_matter_from_request(session, agent_request)
            memory_artifacts = await session_svc.maybe_persist_long_term_memory(
                session, response, task_type
            )
            if memory_artifacts:
                response.artifacts = {**response.artifacts, **memory_artifacts}
            session_svc.update_summary(session)
            session_svc.persist(session)

        latency_ms = (time.perf_counter() - started) * 1000
        self.hooks.emit(
            Latency(operation="orchestrator.handle", latency_ms=latency_ms)
        )
        return response

    @staticmethod
    def _validate_review_payload(request: AgentRequest, context: dict) -> None:
        session_block = context.get("session") or {}
        matter = session_block.get("matter") or {}
        settings = get_settings()

        contract_text = (
            context.get("contract_text")
            or request.contract_text
            or matter.get("contract_text")
            or request.query
            or ""
        ).strip()
        contract_document_id = (
            request.contract_document_id
            or context.get("contract_document_id")
            or matter.get("contract_document_id")
            or ""
        )
        if isinstance(contract_document_id, str):
            contract_document_id = contract_document_id.strip()
        else:
            contract_document_id = str(contract_document_id or "").strip()

        if not contract_text and not contract_document_id:
            raise ReviewPayloadError(
                "Review requires contract_text or contract_document_id"
            )

        if settings.review_require_contract_document_id and not contract_document_id:
            raise ReviewPayloadError(
                "Review requires contract_document_id when "
                "REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true"
            )

        policies = request.policies or context.get("policies") or matter.get("policies") or []
        if settings.review_reject_inline_policies and _has_inline_policy_texts(policies):
            raise ReviewPayloadError(
                "Inline policy text is not allowed when REVIEW_REJECT_INLINE_POLICIES=true; "
                "sync policies to document-mcp and use policy_document_ids or policy_refs"
            )

    def _raise_agent_not_found(self, task_type: str) -> None:
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


def _has_inline_policy_texts(policies: list) -> bool:
    for policy in policies:
        if isinstance(policy, dict):
            text = policy.get("text") or ""
        else:
            text = getattr(policy, "text", "") or ""
        if str(text).strip():
            return True
    return False
