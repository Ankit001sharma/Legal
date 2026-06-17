"""API Gateway — single entry point for all agent requests.

Architecture:
    Client → POST /query → QueryOrchestrator → AgentRegistry → Agent → MCP servers

Research and contract review both use this gateway only (no separate public review API).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

from legal_ai_platform.container import PlatformContainer, get_container
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError, ReviewPayloadError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage platform container lifecycle."""
    container = get_container()
    app.state.container = container
    yield
    await container.shutdown()


app = FastAPI(
    title="Legal AI Platform",
    description="Unified API gateway for research, contract review, and future legal agents",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check including downstream MCP servers."""
    container: PlatformContainer = app.state.container
    checks: dict[str, Any] = {"status": "ok", "service": "legal-ai-platform", "version": "0.2.0"}

    try:
        retrieval = await container.retrieval_client.health()
        checks["retrieval_mcp"] = retrieval.get("status", "unknown")
    except Exception as exc:  # noqa: BLE001
        checks["retrieval_mcp"] = f"error: {exc}"
        checks["status"] = "degraded"

    try:
        document = await container.document_client.health()
        checks["document_mcp"] = document.get("status", "unknown")
    except Exception as exc:  # noqa: BLE001
        checks["document_mcp"] = f"error: {exc}"
        checks["status"] = "degraded"

    return checks


@app.get("/agents")
async def list_agents() -> dict[str, Any]:
    """List agents registered with the orchestrator."""
    container: PlatformContainer = app.state.container
    return {
        "agents": container.registry.discover(),
        "entrypoint": "POST /query",
    }


@app.get("/sessions/{thread_id}")
async def get_session(thread_id: str, tenant_id: str = "default") -> dict[str, Any]:
    """Read unified session state (transcript, matter, summary)."""
    container: PlatformContainer = app.state.container
    state = container.session_service.get_session(tenant_id, thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {thread_id}")
    return state.model_dump(mode="json")


@app.delete("/sessions/{thread_id}")
async def delete_session(
    thread_id: str,
    tenant_id: str = "default",
    cleanup_legacy_research: bool = True,
) -> dict[str, Any]:
    """Delete unified session state for a thread."""
    container: PlatformContainer = app.state.container
    result = container.session_service.delete_session(
        tenant_id,
        thread_id,
        cleanup_legacy_research=cleanup_legacy_research,
    )
    if not result["deleted"] and not result["legacy_research_files_removed"]:
        raise HTTPException(status_code=404, detail=f"Session not found: {thread_id}")
    return result


@app.post("/query", response_model=AgentResponse)
async def query(body: AgentRequest) -> AgentResponse:
    """Submit a request to the orchestrator (research, review, or future agents)."""
    container: PlatformContainer = app.state.container
    started = time.perf_counter()
    logger.info(
        "query received task_type=%s query_len=%d thread_id=%s",
        body.task_type,
        len(body.query),
        body.thread_id,
    )
    try:
        response = await container.orchestrator.handle(body)
        logger.info(
            "query completed agent=%s task_type=%s success=%s elapsed_s=%.1f",
            response.agent,
            response.task_type,
            response.success,
            time.perf_counter() - started,
        )
        return response
    except ReviewPayloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
