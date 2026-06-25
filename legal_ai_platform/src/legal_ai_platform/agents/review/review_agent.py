"""Contract compliance review agent (LangGraph + document-mcp + retrieval memory)."""

from __future__ import annotations

import time

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.mcp.document_client import DocumentMCPClient
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from review_agent.graph.review_graph import run_review
from review_agent.services.review_preflight import ReviewPreflightError


class ReviewAgent(BaseAgent):
    """Contract compliance review against pre-indexed policies in document-mcp."""

    agent_type = "review"

    def __init__(
        self,
        document_client: DocumentMCPClient,
        retrieval_client: RetrievalMCPClient | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        super().__init__(hooks=hooks)
        self._document_client = document_client
        self._retrieval_client = retrieval_client

    async def execute(self, request: AgentRequest) -> AgentResponse:
        """Run review — contract_text or contract_document_id; policies from tenant index by default."""
        context = request.effective_context()
        contract_document_id = request.contract_document_id or context.get("contract_document_id")
        contract_text = request.contract_text or context.get("contract_text")
        policy_document_ids = request.policy_document_ids or context.get("policy_document_ids")
        policy_source = request.policy_source or context.get("policy_source") or "indexed"
        session_block = context.get("session") or {}
        memory_snippets = session_block.get("memory_snippets") or ""
        platform_owns_memory = bool(session_block.get("platform_owns_long_term_memory"))
        memory_client = (
            None if platform_owns_memory else self._retrieval_client
        )

        if not contract_document_id and not (contract_text or "").strip():
            return AgentResponse(
                agent=self.agent_type,
                task_type="review",
                success=False,
                error="contract_text or contract_document_id is required",
                thread_id=request.thread_id,
            )
        if policy_source == "session" and not policy_document_ids:
            return AgentResponse(
                agent=self.agent_type,
                task_type="review",
                success=False,
                error="policy_document_ids is required when policy_source=session",
                thread_id=request.thread_id,
            )

        started = time.perf_counter()
        try:
            result = await run_review(
                client=self._document_client,  # type: ignore[arg-type]
                tenant_id=request.tenant_id or "default",
                contract_document_id=str(contract_document_id) if contract_document_id else None,
                contract_text=str(contract_text).strip() if contract_text else None,
                contract_title=context.get("contract_title", "Contract"),
                policy_document_ids=[str(doc_id) for doc_id in policy_document_ids]
                if policy_document_ids
                else None,
                policy_scope="request" if policy_source == "session" else "indexed",
                contract_type=context.get("contract_type"),
                policy_type=context.get("policy_type"),
                memory_client=memory_client,
                memory_context=str(memory_snippets),
                thread_id=request.thread_id,
            )
            report = result.get("report")
            latency_ms = (time.perf_counter() - started) * 1000
            if report is None:
                return AgentResponse(
                    agent=self.agent_type,
                    task_type="review",
                    success=False,
                    error="review produced no report",
                    events=[{"latency_ms": latency_ms}],
                )

            return AgentResponse(
                agent=self.agent_type,
                task_type="review",
                output=report.summary_markdown,
                artifacts={
                    "report": report.model_dump(mode="json"),
                    "audit": report.metadata.get("artifact"),
                    "memory_saved": result.get("memory_saved", False),
                    "memory_context": result.get("memory_context", ""),
                    "contract_document_id": str(contract_document_id),
                    "policy_document_ids": [str(doc_id) for doc_id in policy_document_ids],
                },
                success=True,
                thread_id=result.get("thread_id") or request.thread_id,
                events=[
                    {
                        "latency_ms": latency_ms,
                        "finding_count": len(report.findings),
                        "memory_hits": len(result.get("memory_hits") or []),
                    }
                ],
            )
        except ReviewPreflightError as exc:
            return AgentResponse(
                agent=self.agent_type,
                task_type="review",
                success=False,
                error=f"preflight failed: {exc}",
                thread_id=request.thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResponse(
                agent=self.agent_type,
                task_type="review",
                success=False,
                error=str(exc),
                thread_id=request.thread_id,
            )
