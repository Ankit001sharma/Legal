"""Research Agent — delegates to the appropriate strategy based on ResearchMode."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.agents.research.strategies import (
    DeepResearchStrategy,
    NormalResearchStrategy,
)
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.research import ResearchMode, ResearchRequest, ResearchResponse
from legal_ai_platform.models.retrieval import RetrievalResult
from legal_ai_platform.observability.events import (
    Failure,
    Latency,
    ResearchCompleted,
    ResearchModeSelected,
)
from legal_ai_platform.observability.hooks import HookRegistry

logger = logging.getLogger(__name__)


# LangGraph nodes that produce LLM tokens we want to forward to the client in real-time.
_STREAM_NODES: frozenset[str] = frozenset(
    {"generate_normal_answer", "finalize_report", "final_report_generation"}
)

# Progress events emitted by the research graph (see status_stream.py).
_PROGRESS_EVENTS: frozenset[str] = frozenset(
    {"group_start", "sub_step", "group_end", "done"}
)


def _extract_progress_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    """Pull a progress dict from LangGraph v2 stream events.

    ``get_stream_writer()`` payloads only surface when ``astream_events`` is
    called with ``stream_mode`` that includes ``"custom"``. They arrive as
    ``on_chain_stream`` chunks shaped like ``("custom", {...})`` or, on older
    paths, as ``on_custom_event``.
    """
    evt = event.get("event", "")

    if evt == "on_custom_event":
        data = event.get("data") or {}
        if isinstance(data, dict) and data.get("event") in _PROGRESS_EVENTS:
            return data
        return None

    if evt != "on_chain_stream":
        return None

    chunk = (event.get("data") or {}).get("chunk")
    if chunk is None:
        return None

    payload: Any = chunk
    if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "custom":
        payload = chunk[1]

    if isinstance(payload, dict) and payload.get("event") in _PROGRESS_EVENTS:
        return payload
    return None


def _extract_confidence(report: str) -> str:
    """Parse the confidence level stated in the 'Brief Direct Answer' section."""
    m = re.search(
        r"Confidence:\s*\*\*\[?\s*(ESTABLISHED|LIKELY|UNCERTAIN|NOT[_ ]FOUND)\s*\]?\*\*",
        report or "",
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper().replace(" ", "_")
    if re.search(
        r"\b(unsettled|unclear|ambiguous|no binding precedent|conflicting views)\b",
        report or "",
        re.IGNORECASE,
    ):
        return "UNCERTAIN"
    return "ESTABLISHED"


def _effective_session_id(session_id: str | None) -> str:
    return session_id or f"guest-{uuid.uuid4()}"


def _build_run_config(request: AgentRequest, session_id: str) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "thread_id": session_id,
        "tenant_id": request.tenant_id,
        "research_mode": request.mode.value,
    }
    if request.user_id:
        configurable["user_id"] = request.user_id
    if request.role:
        configurable["role"] = request.role
    auth_token = request.context.get("auth_token")
    if auth_token:
        configurable["auth_token"] = auth_token
    # LangGraph default recursion_limit is 25, which is too low for the deep
    # research supervisor loop (up to 15 iterations × 3 nodes = 45+ steps).
    return {"configurable": configurable, "recursion_limit": 150}


def _chunk_text(chunk_content: Any) -> str:
    """Extract a plain-string token from an AIMessageChunk content field."""
    if isinstance(chunk_content, str):
        return chunk_content
    if isinstance(chunk_content, list):
        return "".join(
            b.get("text", "") for b in chunk_content if isinstance(b, dict)
        )
    return ""


class ResearchAgent(BaseAgent):
    """Legal research agent backed by two LangGraph pipelines.

    Mode selection:
      ResearchMode.NORMAL (default) → NormalResearchStrategy  — fast, concise
      ResearchMode.DEEP             → DeepResearchStrategy    — exhaustive memo

    Both strategies share the same retrieval infrastructure and memory backend.
    The compiled graphs are created once at startup and reused across requests.
    """

    agent_type = "research"

    def __init__(
        self,
        retrieval_client: RetrievalMCPClient,
        hooks: HookRegistry | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(hooks=hooks)
        self._retrieval_client = retrieval_client
        self._timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        # Compile both strategies once at agent startup.
        self._strategies = {
            ResearchMode.NORMAL: NormalResearchStrategy(),
            ResearchMode.DEEP: DeepResearchStrategy(),
        }

    async def execute(self, request: AgentRequest) -> AgentResponse:
        """Run the research pipeline for the given query and mode."""
        session_id = _effective_session_id(request.session_id)
        mode = request.mode  # already has a default of NORMAL from AgentRequest

        self.hooks.emit(
            ResearchModeSelected(
                mode=mode.value,
                query_length=len(request.query),
            )
        )

        research_request = ResearchRequest(
            query=request.query,
            mode=mode,
            context=request.context,
            tenant_id=request.tenant_id,
            max_results=request.max_results,
            session_id=session_id,
            user_id=request.user_id,
            role=request.role,
        )
        run_config = _build_run_config(request, session_id)

        strategy = self._strategies[mode]
        graph = strategy.graph

        logger.info(
            "research execute start mode=%s session_id=%s query_len=%d tenant_id=%s",
            mode.value,
            session_id,
            len(request.query),
            request.tenant_id,
        )

        started = time.perf_counter()
        try:
            coro = graph.ainvoke(
                {"messages": [HumanMessage(content=research_request.query)]},
                config=run_config,
            )
            if self._timeout_seconds is not None:
                result = await asyncio.wait_for(coro, timeout=self._timeout_seconds)
            else:
                result = await coro

            research_response = self._build_research_response(result)
            research_directions = result.get("research_directions") or []
            latency_ms = (time.perf_counter() - started) * 1000
            metric_fields = self._metrics_from_response(research_response)

            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            self.hooks.emit(
                ResearchCompleted(
                    mode=mode.value,
                    citations_found=len(research_response.sources),
                    output_length=len(research_response.report),
                    latency_ms=latency_ms,
                    **metric_fields,
                )
            )
            logger.info(
                "research execute complete mode=%s session_id=%s sources=%d output_len=%d latency_ms=%.0f awaiting_input=%s",
                mode.value,
                session_id,
                len(research_response.sources),
                len(research_response.report),
                latency_ms,
                research_response.awaiting_input,
            )

            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output=research_response.report,
                artifacts={
                    "research": research_response.model_dump(),
                    "mode": mode.value,
                },
                success=True,
                session_id=session_id,
                awaiting_input=research_response.awaiting_input,
                research_directions=research_directions,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "research execute timeout mode=%s session_id=%s timeout_s=%s latency_ms=%.0f",
                mode.value,
                session_id,
                self._timeout_seconds,
                latency_ms,
            )
            self.hooks.emit(
                Failure(
                    operation="research_agent.execute",
                    error=f"timed out after {self._timeout_seconds}s",
                    recoverable=False,
                )
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output="",
                error=f"Research timed out after {self._timeout_seconds}s",
                success=False,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "research execute failed mode=%s session_id=%s latency_ms=%.0f",
                mode.value,
                session_id,
                latency_ms,
            )
            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output="",
                error=str(exc),
                success=False,
                session_id=session_id,
            )

    async def execute_sse_stream(
        self, request: AgentRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream research for POST /query SSE contract.

        Yields Claude-style step progress events:
          ``group_start`` / ``sub_step`` / ``group_end`` / ``done``
        plus ``content`` answer chunks and final ``artifacts``.
        """
        session_id = _effective_session_id(request.session_id)
        mode = request.mode
        run_config = _build_run_config(request, session_id)
        strategy = self._strategies[mode]
        graph = strategy.graph
        started = time.perf_counter()
        streaming_started = False
        progress_done_emitted = False

        logger.info(
            "research sse stream start mode=%s session_id=%s query_len=%d",
            mode.value,
            session_id,
            len(request.query),
        )

        try:
            async for event in graph.astream_events(
                {"messages": [HumanMessage(content=request.query)]},
                config=run_config,
                version="v2",
                stream_mode=["updates", "custom"],
            ):
                progress = _extract_progress_payload(event)
                if progress is not None:
                    if progress.get("event") == "done":
                        progress_done_emitted = True
                    yield progress
                    continue

                evt = event.get("event", "")
                metadata = event.get("metadata", {})
                node = metadata.get("langgraph_node") or event.get("name", "")

                if evt == "on_chat_model_stream" and node in _STREAM_NODES:
                    chunk = (event.get("data") or {}).get("chunk")
                    if chunk:
                        text = _chunk_text(getattr(chunk, "content", ""))
                        if text:
                            streaming_started = True
                            yield {"content": text}

            snapshot = await graph.aget_state(run_config)
            final_state: dict[str, Any] = (
                dict(snapshot.values) if snapshot and snapshot.values else {}
            )
            research_response = self._build_research_response(final_state)
            final_report = research_response.report

            if final_report and not streaming_started:
                chunk_size = 120
                for i in range(0, len(final_report), chunk_size):
                    chunk = final_report[i : i + chunk_size]
                    if chunk:
                        yield {"content": chunk}

            latency_ms = (time.perf_counter() - started) * 1000
            metric_fields = self._metrics_from_response(research_response)
            self.hooks.emit(
                Latency(operation="research_agent.execute_sse_stream", latency_ms=latency_ms)
            )
            self.hooks.emit(
                ResearchCompleted(
                    mode=mode.value,
                    citations_found=len(research_response.sources),
                    output_length=len(final_report or ""),
                    latency_ms=latency_ms,
                    **metric_fields,
                )
            )

            if not progress_done_emitted:
                yield {"event": "done", "timestamp_ms": int(time.time() * 1000)}

            yield {
                "artifacts": {
                    "research": research_response.model_dump(),
                    "mode": mode.value,
                }
            }

        except asyncio.TimeoutError:
            self.hooks.emit(
                Failure(
                    operation="research_agent.execute_sse_stream",
                    error=f"timed out after {self._timeout_seconds}s",
                    recoverable=False,
                )
            )
            yield {
                "content": f"Research timed out after {self._timeout_seconds}s",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "research sse stream failed mode=%s session_id=%s",
                mode.value,
                session_id,
            )
            self.hooks.emit(
                Failure(
                    operation="research_agent.execute_sse_stream",
                    error=str(exc),
                    recoverable=False,
                )
            )
            yield {"content": f"Error: {exc}"}

    def _build_research_response(self, state: dict[str, Any]) -> ResearchResponse:
        """Map LangGraph final state to ResearchResponse."""
        verification = state.get("verification")
        verification_dict = None
        if verification is not None:
            verification_dict = (
                verification.model_dump()
                if hasattr(verification, "model_dump")
                else dict(verification)
            )

        metrics_obj = state.get("research_metrics")
        metrics_dict = None
        if metrics_obj is not None:
            metrics_dict = (
                metrics_obj.model_dump()
                if hasattr(metrics_obj, "model_dump")
                else dict(metrics_obj)
            )
        elif verification_dict and verification_dict.get("metrics"):
            metrics_dict = verification_dict["metrics"]

        claims_raw = state.get("validated_claims") or []
        claims = [
            c.model_dump() if hasattr(c, "model_dump") else dict(c)
            for c in claims_raw
        ]

        final_report = state.get("final_report")
        awaiting_input = not bool(final_report)
        report = final_report or self._last_ai_text(state)

        sources = self._map_retrieved_sources(state.get("retrieved_sources") or [])

        return ResearchResponse(
            report=report,
            research_brief=state.get("research_brief"),
            sources=sources,
            raw_notes=state.get("raw_notes", []),
            verification=verification_dict,
            metrics=metrics_dict,
            claims=claims,
            awaiting_input=awaiting_input,
        )

    @staticmethod
    def _metrics_from_response(response: ResearchResponse) -> dict[str, float]:
        """Extract observability metric fields from a research response."""
        metrics = response.metrics or {}
        if not metrics and response.verification:
            metrics = response.verification.get("metrics") or {}
        return {
            "citation_coverage_pct": float(metrics.get("citation_coverage_pct", 0.0)),
            "unsupported_claim_pct": float(metrics.get("unsupported_claim_pct", 0.0)),
            "hallucination_rate_pct": float(metrics.get("hallucination_rate_pct", 0.0)),
            "source_quality_score": float(metrics.get("source_quality_score", 0.0)),
            "relevance_score": float(metrics.get("relevance_score", 0.0)),
            "coverage_completeness_pct": float(
                metrics.get("coverage_completeness_pct", 0.0)
            ),
            "consensus_score": float(metrics.get("consensus_score", 0.0)),
            "overall_confidence_pct": float(metrics.get("overall_confidence_pct", 0.0)),
        }

    @staticmethod
    def _map_retrieved_sources(raw_sources: list[Any]) -> list[RetrievalResult]:
        """Convert graph-state RetrievedSource objects to API RetrievalResult."""
        mapped: list[RetrievalResult] = []
        for item in raw_sources:
            if hasattr(item, "model_dump"):
                data = item.model_dump()
            elif isinstance(item, dict):
                data = item
            else:
                continue
            url = str(data.get("url") or "")
            source_type = str(data.get("source_type") or "web")
            mapped.append(
                RetrievalResult(
                    source=source_type,
                    title=str(data.get("title") or ""),
                    url=url,
                    content=str(data.get("excerpt") or ""),
                    citation=str(data.get("citation") or ""),
                    score=1.0 if data.get("fetched") else 0.5,
                    metadata={
                        "authority_tier": data.get("authority_tier"),
                        "fetched": data.get("fetched"),
                        "source_type": source_type,
                    },
                )
            )
        return mapped

    @staticmethod
    def _last_ai_text(state: dict[str, Any]) -> str:
        """Return the content of the last assistant message, if any."""
        for message in reversed(state.get("messages", []) or []):
            content = getattr(message, "content", None)
            msg_type = getattr(message, "type", None)
            if msg_type == "ai" and isinstance(content, str) and content.strip():
                return content
        return ""

    async def check_retrieval_health(self) -> dict[str, Any]:
        """Check that the Legal ai retrieval MCP server is reachable."""
        return await self._retrieval_client.health()
