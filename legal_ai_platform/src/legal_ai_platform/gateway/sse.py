"""SSE helpers and query streaming for POST /query."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import Request

from legal_ai_platform.container import PlatformContainer
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError

logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def wants_sse_stream(request: Request) -> bool:
    """Return True when the client requested an SSE response."""
    accept = (request.headers.get("accept") or "").lower()
    return "text/event-stream" in accept


def sse_line(payload: dict) -> str:
    """Format one SSE ``data:`` line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    """Terminal SSE event."""
    return "data: [DONE]\n\n"


def agent_response_to_sse_events(response: AgentResponse) -> list[dict]:
    """Convert a synchronous AgentResponse to SSE payload dicts."""
    events: list[dict] = []
    if response.output:
        events.append({"content": response.output})
    if response.artifacts:
        events.append({"artifacts": response.artifacts})
    return events


async def stream_query(
    container: PlatformContainer,
    body: AgentRequest,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Yield SSE lines for a /query request."""
    try:
        task_type, agent = container.orchestrator.resolve(body)
        logger.info("query stream task_type=%s agent=%s", task_type, agent.agent_type)

        execute_sse = getattr(agent, "execute_sse_stream", None)
        if execute_sse is not None:
            async for event in execute_sse(body):
                if await request.is_disconnected():
                    break
                if not event:
                    continue
                if "content" in event and not str(event.get("content") or ""):
                    continue
                yield sse_line(event)
                await asyncio.sleep(0)
        else:
            yield sse_line({"status": "thinking", "label": "Analyzing your query…"})
            await asyncio.sleep(0)
            response = await agent.execute(body)
            response.task_type = task_type
            for event in agent_response_to_sse_events(response):
                yield sse_line(event)
                await asyncio.sleep(0)

        yield sse_done()
    except AgentNotFoundError as exc:
        yield sse_line({"content": str(exc)})
        yield sse_done()
    except Exception as exc:  # noqa: BLE001
        logger.exception("query stream failed")
        yield sse_line({"content": f"Error: {exc}"})
        yield sse_done()


def sse_response_headers() -> dict[str, str]:
    return dict(_SSE_HEADERS)
