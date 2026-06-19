"""Research Agent — delegates to the appropriate strategy based on ResearchMode."""

from __future__ import annotations

import asyncio
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


# LangGraph nodes that produce LLM tokens we want to forward to the client in real-time.
_STREAM_NODES: frozenset[str] = frozenset({"generate_normal_answer", "finalize_report"})

# Nodes whose start should trigger a summarization status event.
_SUMMARIZE_NODES: frozenset[str] = frozenset({"compress_research"})

# Tool names that perform retrieval searches.
_SEARCH_TOOLS: frozenset[str] = frozenset({"web_search", "semantic_search"})

# Human-readable labels for each LangGraph node, shown in the streaming UI.
_NODE_PROGRESS: dict[str, tuple[str, str]] = {
    "load_memory": ("🧠", "Loading conversation memory"),
    "compact_conversation": ("📋", "Preparing context"),
    "write_research_brief": ("📝", "Understanding your query"),
    "normal_researcher": ("🔍", "Searching legal databases"),
    "generate_normal_answer": ("⚖️", "Drafting legal answer"),
    "clarify_with_user": ("💭", "Analysing research scope"),
    "lead_researcher": ("🔬", "Coordinating deep research"),
    "finalize_report": ("📜", "Drafting legal memorandum"),
    "compress_research": ("🗜️", "Compiling research findings"),
}


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


def _search_status(query: str) -> dict[str, str]:
    """Build a searching status payload from a tool query string."""
    lowered = (query or "").lower()
    if "indiankanoon" in lowered:
        label = "Querying Indian Kanoon"
    elif "indiacode" in lowered:
        label = "Querying India Code"
    else:
        label = "Querying case law database"
    return {"status": "searching", "label": label, "query": query}


def _crawl_status(url: str) -> dict[str, str]:
    """Build a crawling status payload for a fetch_url call."""
    lowered = (url or "").lower()
    if "indiankanoon" in lowered:
        label = "Reading judgment"
    elif "sci.gov.in" in lowered or "supremecourt" in lowered:
        label = "Reading court order"
    else:
        label = "Reading source"
    return {"status": "crawling", "label": label, "url": url}


def _summarize_status(source_count: int) -> dict[str, str]:
    if source_count > 0:
        label = f"Processing {source_count} retrieved source{'s' if source_count != 1 else ''}"
    else:
        label = "Processing retrieved sources"
    return {"status": "summarizing", "label": label}


def _effective_session_id(session_id: str | None) -> str:
    return session_id or f"guest-{uuid.uuid4()}"


def _build_run_config(request: AgentRequest, session_id: str) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "thread_id": session_id,
        "tenant_id": request.tenant_id,
    }
    if request.user_id:
        configurable["user_id"] = request.user_id
    if request.role:
        configurable["role"] = request.role
    auth_token = request.context.get("auth_token")
    if auth_token:
        configurable["auth_token"] = auth_token
    return {"configurable": configurable}


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
                session_id=session_id,
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
                session_id=session_id,
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
                session_id=session_id,
            )

    async def execute_stream(
        self, request: AgentRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream research pipeline: node-level progress events + final answer tokens.

        Yields dicts with ``type`` field:
          ``progress``     — a node started  {icon, node, message}
          ``stream_start`` — final answer is about to stream
          ``token``        — one chunk of the final answer  {text}
          ``done``         — graph finished  {success, session_id, output, …}
          ``error``        — unrecoverable failure  {message}
        """
        session_id = _effective_session_id(request.session_id)
        mode = request.mode
        run_config = _build_run_config(request, session_id)
        strategy = self._strategies[mode]
        graph = strategy.graph
        started = time.perf_counter()
        reported_nodes: set[str] = set()
        streaming_started = False  # True once first LLM token is forwarded

        try:
            async for event in graph.astream_events(
                {"messages": [HumanMessage(content=request.query)]},
                config=run_config,
                version="v2",
            ):
                evt = event.get("event", "")
                metadata = event.get("metadata", {})
                # LangGraph v2 exposes the node name in metadata.langgraph_node
                node = metadata.get("langgraph_node") or event.get("name", "")

                # Progress events — one per node, shown while the graph is running.
                if (
                    evt == "on_chain_start"
                    and node in _NODE_PROGRESS
                    and node not in reported_nodes
                ):
                    reported_nodes.add(node)
                    icon, message = _NODE_PROGRESS[node]
                    yield {
                        "type": "progress",
                        "node": node,
                        "icon": icon,
                        "message": message,
                    }

                # Real LLM token streaming from answer-generation nodes.
                # The raw tokens are forwarded immediately; the post-processed
                # (linkified, verified) version is sent via stream_replace once
                # the graph finishes and the final state is available.
                elif evt == "on_chat_model_stream" and node in _STREAM_NODES:
                    chunk = (event.get("data") or {}).get("chunk")
                    if chunk:
                        text = _chunk_text(getattr(chunk, "content", ""))
                        if text:
                            if not streaming_started:
                                streaming_started = True
                                yield {"type": "stream_start"}
                            yield {"type": "token", "text": text}

            # Graph has finished; fetch the final post-processed state.
            snapshot = await graph.aget_state(run_config)
            final_state: dict[str, Any] = (
                dict(snapshot.values) if snapshot and snapshot.values else {}
            )

            research_response = self._build_research_response(final_state)
            final_report = research_response.report

            if final_report and not research_response.awaiting_input:
                if streaming_started:
                    # Replace the raw streamed tokens with the post-processed version
                    # (citations linkified, deterministic checks appended, etc.).
                    yield {"type": "stream_replace", "text": final_report}
                else:
                    # No streaming happened (model doesn't support it or was skipped);
                    # fall back to chunked emission.
                    yield {"type": "stream_start"}
                    chunk_size = 30
                    for i in range(0, len(final_report), chunk_size):
                        yield {"type": "token", "text": final_report[i : i + chunk_size]}

            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Latency(operation="research_agent.execute_stream", latency_ms=latency_ms)
            )
            self.hooks.emit(
                ResearchCompleted(
                    mode=mode.value,
                    citations_found=len(research_response.sources),
                    output_length=len(final_report or ""),
                    latency_ms=latency_ms,
                )
            )

            yield {
                "type": "done",
                "success": True,
                "session_id": session_id,
                "awaiting_input": research_response.awaiting_input,
                "research_directions": final_state.get("research_directions") or [],
                "output": final_report,
                "confidence_level": _extract_confidence(final_report or ""),
                "artifacts": {
                    "research": research_response.model_dump(),
                    "mode": mode.value,
                },
            }

        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Failure(
                    operation="research_agent.execute_stream",
                    error=str(exc),
                    recoverable=False,
                )
            )
            yield {"type": "error", "message": str(exc), "session_id": session_id}

    async def execute_sse_stream(
        self, request: AgentRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream research for POST /query SSE contract.

        Yields dict payloads mapped to ``data: <json>`` lines:
          ``status``   — live activity feed (thinking/searching/crawling/…)
          ``content``  — answer text chunks
          ``artifacts``— research sidebar payload (emitted once at end)
        """
        session_id = _effective_session_id(request.session_id)
        mode = request.mode
        run_config = _build_run_config(request, session_id)
        strategy = self._strategies[mode]
        graph = strategy.graph
        started = time.perf_counter()
        fetch_count = 0
        summarizing_emitted = False
        drafting_emitted = False
        streaming_started = False

        yield {"status": "thinking", "label": "Analyzing your query…"}

        try:
            async for event in graph.astream_events(
                {"messages": [HumanMessage(content=request.query)]},
                config=run_config,
                version="v2",
            ):
                evt = event.get("event", "")
                metadata = event.get("metadata", {})
                node = metadata.get("langgraph_node") or event.get("name", "")
                tool_name = event.get("name", "")

                if evt == "on_custom_event":
                    data = event.get("data") or {}
                    if isinstance(data, dict) and data.get("status"):
                        if data.get("status") == "crawling":
                            fetch_count += 1
                        yield data

                elif evt == "on_tool_start":
                    tool_input = (event.get("data") or {}).get("input") or {}
                    if tool_name in _SEARCH_TOOLS:
                        query = str(tool_input.get("query") or "")
                        yield _search_status(query)
                    elif tool_name == "fetch_url":
                        url = str(tool_input.get("url") or "")
                        if url:
                            fetch_count += 1
                            yield _crawl_status(url)

                elif evt == "on_chain_start" and node in _SUMMARIZE_NODES:
                    if not summarizing_emitted:
                        summarizing_emitted = True
                        yield _summarize_status(fetch_count)

                elif evt == "on_chain_start" and node in _STREAM_NODES:
                    if not summarizing_emitted and fetch_count:
                        summarizing_emitted = True
                        yield _summarize_status(fetch_count)
                    if not drafting_emitted:
                        drafting_emitted = True
                        yield {"status": "drafting", "label": "Drafting legal analysis…"}

                elif evt == "on_chat_model_stream" and node in _STREAM_NODES:
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
            self.hooks.emit(
                Latency(operation="research_agent.execute_sse_stream", latency_ms=latency_ms)
            )
            self.hooks.emit(
                ResearchCompleted(
                    mode=mode.value,
                    citations_found=len(research_response.sources),
                    output_length=len(final_report or ""),
                    latency_ms=latency_ms,
                )
            )

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
