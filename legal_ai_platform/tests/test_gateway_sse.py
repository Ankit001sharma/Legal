"""Tests for POST /query SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.container import PlatformContainer, reset_container
from legal_ai_platform.gateway.app import app
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.task_types import TaskType
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry

_SESSION = "frontend-session-1"


class _StubSSEAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="research",
            output=f"Report for: {request.query}",
        )

    async def execute_sse_stream(
        self, request: AgentRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"status": "thinking", "label": "Analyzing your query…"}
        yield {"status": "searching", "label": "Querying Indian Kanoon", "query": "test"}
        yield {
            "status": "crawling",
            "label": "Reading judgment",
            "url": "https://indiankanoon.org/doc/1/",
        }
        yield {"status": "drafting", "label": "Drafting legal analysis…"}
        yield {"content": "Answer text"}
        yield {"artifacts": {"research": {"sources": [], "report": "Answer text"}}}


@pytest.fixture
def sse_client(monkeypatch):
    reset_container()
    registry = AgentRegistry()
    registry.register("research", _StubSSEAgent())
    container = PlatformContainer()
    container.registry = registry
    container.orchestrator = QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=container.hooks,
    )
    app.state.container = container
    yield TestClient(app)
    reset_container()


def _parse_sse_events(body: str) -> list[Any]:
    events: list[Any] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        payload = block[6:]
        if payload == "[DONE]":
            events.append("[DONE]")
        else:
            events.append(json.loads(payload))
    return events


def test_query_returns_json_without_sse_accept_header(sse_client):
    response = sse_client.post(
        "/query",
        json={"query": "IPC 420?", "task_type": ["research"], "session_id": _SESSION},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["output"].startswith("Report for:")
    assert data["agent"] == "research"


def test_query_streams_sse_with_accept_header(sse_client):
    with sse_client.stream(
        "POST",
        "/query",
        json={"query": "privacy?", "task_type": ["research"], "session_id": _SESSION},
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    assert events[0] == {"status": "thinking", "label": "Analyzing your query…"}
    assert events[1]["status"] == "searching"
    assert events[2]["status"] == "crawling"
    assert events[2]["url"] == "https://indiankanoon.org/doc/1/"
    assert {"content": "Answer text"} in events
    assert events[-1] == "[DONE]"


def test_query_sse_status_events_precede_content(sse_client):
    with sse_client.stream(
        "POST",
        "/query",
        json={"query": "test", "task_type": ["research"], "session_id": _SESSION},
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    first_content_idx = next(
        i for i, e in enumerate(events) if isinstance(e, dict) and "content" in e
    )
    status_idxs = [i for i, e in enumerate(events) if isinstance(e, dict) and "status" in e]
    assert status_idxs
    assert max(status_idxs) < first_content_idx


def test_query_sse_always_ends_with_done(sse_client):
    with sse_client.stream(
        "POST",
        "/query",
        json={"query": "test", "task_type": ["research"], "session_id": _SESSION},
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    assert events[-1] == "[DONE]"


def test_query_sse_agent_not_found_emits_done(sse_client):
    registry = AgentRegistry()
    app.state.container.registry = registry
    app.state.container.orchestrator = QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=app.state.container.hooks,
    )

    with sse_client.stream(
        "POST",
        "/query",
        json={"query": "test", "task_type": [TaskType.CONTRACT], "session_id": _SESSION},
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    events = _parse_sse_events(body)
    assert events[-1] == "[DONE]"
    assert "content" in events[0]
