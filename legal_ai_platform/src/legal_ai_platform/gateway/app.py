"""API Gateway — single entry point for client requests.

Architecture:
    Client → POST /query → QueryOrchestrator → AgentRegistry → Agent
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from legal_ai_platform.auth.dependencies import (
    enrich_agent_request,
    get_current_principal,
    get_db,
    get_optional_bearer_token,
)
from legal_ai_platform.auth.principal import Principal
from legal_ai_platform.container import PlatformContainer, get_container
from legal_ai_platform.db.session import init_db
from legal_ai_platform.gateway.auth_routes import router as auth_router
from legal_ai_platform.gateway.sse import (
    sse_response_headers,
    stream_query,
    wants_sse_stream,
)
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage platform container lifecycle."""
    container = get_container()
    init_db(container.settings.database_url)
    app.state.container = container
    yield
    await container.shutdown()


app = FastAPI(
    title="Legal AI Platform",
    description="API Gateway for the Legal AI multi-agent system",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth_router)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "legal-ai-platform", "version": "0.1.0"}


@app.post("/query")
async def query(
    body: AgentRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Depends(get_optional_bearer_token)],
):
    """Submit a legal query to the orchestrator.

    Returns SSE (``text/event-stream``) when the client sends
    ``Accept: text/event-stream``; otherwise returns plain JSON (Format A).
    """
    body = enrich_agent_request(body, principal, db, auth_token=token)
    container: PlatformContainer = app.state.container
    started = time.perf_counter()
    logger.info(
        "query received task_type=%s query_len=%d session_id=%s user_id=%s sse=%s",
        body.task_type,
        len(body.query),
        body.session_id,
        body.user_id,
        wants_sse_stream(request),
    )

    if wants_sse_stream(request):
        return StreamingResponse(
            stream_query(container, body, request),
            media_type="text/event-stream",
            headers=sse_response_headers(),
        )

    try:
        response = await container.orchestrator.handle(body)
        logger.info(
            "query completed success=%s awaiting_input=%s output_len=%d elapsed_s=%.1f",
            response.success,
            response.awaiting_input,
            len(response.output or ""),
            time.perf_counter() - started,
        )
        return response
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/query/stream")
async def query_stream(
    body: AgentRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Depends(get_optional_bearer_token)],
) -> StreamingResponse:
    """Legacy SSE streaming endpoint (progress/token event format)."""
    body = enrich_agent_request(body, principal, db, auth_token=token)
    container: PlatformContainer = app.state.container
    research_agent = container.orchestrator.registry.get("research")
    if research_agent is None:
        raise HTTPException(status_code=404, detail="Research agent not registered")

    async def event_generator():
        try:
            async for event in research_agent.execute_stream(body):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=sse_response_headers(),
    )


# ── Auto-title endpoint ───────────────────────────────────────────────────────

class _TitleRequest(BaseModel):
    query: str


class _TitleResponse(BaseModel):
    title: str


@app.post("/title", response_model=_TitleResponse)
async def generate_title(
    body: _TitleRequest,
    _: Annotated[Principal, Depends(get_current_principal)],
) -> _TitleResponse:
    """Generate a short (4-7 word) chat title from the first user query."""
    try:
        from deep_research_from_scratch.model_config import get_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage

        model = get_chat_model("summarizer")
        resp = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Summarise the following legal query as a clear, specific title "
                        "of 4 to 7 words. Return ONLY the title text — no quotes, no "
                        "punctuation at the end, no explanation."
                    )
                ),
                HumanMessage(content=body.query[:500]),
            ]
        )
        raw = str(getattr(resp, "content", "")).strip().strip("\"'").strip()
        title = raw[:60] if raw else body.query[:56]
        return _TitleResponse(title=title)
    except Exception:  # noqa: BLE001
        # Fall back to the raw query text — never fail the frontend.
        return _TitleResponse(title=body.query[:56])
