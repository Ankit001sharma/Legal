"""Research Agent — delegates to the appropriate strategy based on ResearchMode."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage

from deep_research_from_scratch.platform_session_bridge import (
    activate_platform_session,
    clear_platform_session,
)

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
        thread_id = request.thread_id or str(uuid.uuid4())
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
            thread_id=thread_id,
        )
        run_config = {
            "configurable": {
                "thread_id": thread_id,
                "tenant_id": request.tenant_id,
            }
        }

        strategy = self._strategies[mode]
        graph = strategy.graph

        session_block = request.context.get("session") or {}
        platform_active = bool(session_block.get("platform_owns_session"))

        started = time.perf_counter()
        try:
            if platform_active:
                activate_platform_session(session_block)
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

            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            self.hooks.emit(
                ResearchCompleted(
                    mode=mode.value,
                    citations_found=len(research_response.sources),
                    output_length=len(research_response.report),
                    latency_ms=latency_ms,
                )
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
                thread_id=thread_id,
                awaiting_input=research_response.awaiting_input,
                research_directions=research_directions,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000
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
                thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output="",
                error=str(exc),
                success=False,
                thread_id=thread_id,
            )
        finally:
            if platform_active:
                clear_platform_session()

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
            awaiting_input=awaiting_input,
        )

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
            mapped.append(
                RetrievalResult(
                    source=url or data.get("source_type", "web"),
                    title=str(data.get("title") or ""),
                    url=url,
                    content=str(data.get("excerpt") or ""),
                    citation=str(data.get("citation") or ""),
                    score=1.0 if data.get("fetched") else 0.5,
                    metadata={
                        "authority_tier": data.get("authority_tier"),
                        "fetched": data.get("fetched"),
                        "source_type": data.get("source_type"),
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
