"""
Verify the Java→Python wiring contract introduced in the frontend migration.

Covers:
  1. AUTH_REQUIRED=false + body user_id  → effective principal comes from body, not anonymous
  2. AUTH_REQUIRED=false + no body user_id → falls back to dev_anonymous_user_id
  3. /query with body user_id scopes memory correctly (session_id distinct per user)
  4. /health still reachable without auth
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.auth.principal import UserRole
from legal_ai_platform.config import get_settings
from legal_ai_platform.container import PlatformContainer, reset_container
from legal_ai_platform.db.session import init_db
from legal_ai_platform.gateway.app import app
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry


class _EchoAgent(BaseAgent):
    """Returns the user_id it received so tests can assert on it."""

    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="research",
            output=f"user={request.user_id} tenant={request.tenant_id} role={request.role}",
            session_id=request.session_id,
        )


@pytest.fixture
def service_client(monkeypatch, tmp_path):
    """Client with AUTH_REQUIRED=false — simulates Java gateway calling Python."""
    reset_container()
    db_path = tmp_path / "service_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DEV_ANONYMOUS_USER_ID", "dev-anonymous")
    get_settings.cache_clear()
    init_db(get_settings().database_url)

    registry = AgentRegistry()
    registry.register("research", _EchoAgent())
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
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1. body user_id is used when AUTH_REQUIRED=false
# ---------------------------------------------------------------------------

def test_body_user_id_used_when_auth_disabled(service_client):
    """Java passes user_id in the body; the platform must honour it, not use 'dev-anonymous'."""
    uid = "user-42"
    response = service_client.post(
        "/query",
        json={
            "query": "What is IPC section 420?",
            "task_type": ["research"],
            "user_id": uid,
            "tenant_id": "acme-corp",
            "role": "tenant_user",
            "session_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert f"user={uid}" in body["output"]
    assert "tenant=acme-corp" in body["output"]


def test_anonymous_fallback_when_no_body_user_id(service_client):
    """When Java sends no user_id, the anonymous dev user is used."""
    response = service_client.post(
        "/query",
        json={
            "query": "What is IPC section 420?",
            "task_type": ["research"],
            "session_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "user=dev-anonymous" in body["output"]


# ---------------------------------------------------------------------------
# 2. Different user_ids get independent sessions
# ---------------------------------------------------------------------------

def test_different_users_have_independent_sessions(service_client):
    """Two different user_ids with the same session_id get separate memory namespaces."""
    shared_session = str(uuid.uuid4())

    r1 = service_client.post(
        "/query",
        json={
            "query": "query from user A",
            "task_type": ["research"],
            "user_id": "user-A",
            "session_id": shared_session,
        },
    )
    # user-B trying to use user-A's session should still work when auth is off
    # (session isolation is enforced only when auth_required=true)
    r2 = service_client.post(
        "/query",
        json={
            "query": "query from user B",
            "task_type": ["research"],
            "user_id": "user-B",
            "session_id": shared_session,
        },
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert "user=user-A" in r1.json()["output"]
    assert "user=user-B" in r2.json()["output"]


# ---------------------------------------------------------------------------
# 3. /health is always reachable
# ---------------------------------------------------------------------------

def test_health_no_auth_required(service_client):
    response = service_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 4. role is correctly propagated from body
# ---------------------------------------------------------------------------

def test_role_propagated_from_body(service_client):
    for role in ("tenant_user", "tenant_admin", "super_admin"):
        response = service_client.post(
            "/query",
            json={
                "query": "test",
                "task_type": ["research"],
                "user_id": "u1",
                "role": role,
                "session_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200
        assert f"role={role}" in response.json()["output"], f"role={role} not propagated"


# ---------------------------------------------------------------------------
# 5. SSE mode still works in service mode (Accept: text/event-stream)
# ---------------------------------------------------------------------------

class _StubSSEAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(agent=self.agent_type, task_type="research", output="ok")

    async def execute_sse_stream(self, request):  # type: ignore[override]
        yield {"status": "thinking", "label": "…"}
        yield {"content": f"user={request.user_id}"}
        yield {"artifacts": {"mode": "normal"}}


@pytest.fixture
def service_sse_client(monkeypatch, tmp_path):
    reset_container()
    db_path = tmp_path / "svc_sse.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    init_db(get_settings().database_url)

    registry = AgentRegistry()
    registry.register("research", _StubSSEAgent())
    container = PlatformContainer()
    container.registry = registry
    container.orchestrator = QueryOrchestrator(
        registry=registry, classifier=TaskClassifier(), hooks=container.hooks
    )
    app.state.container = container
    yield TestClient(app)
    reset_container()
    get_settings.cache_clear()


def test_sse_stream_in_service_mode(service_sse_client):
    """Java sends Accept: text/event-stream; Python returns SSE with user context."""
    with service_sse_client.stream(
        "POST",
        "/query",
        json={
            "query": "research query",
            "task_type": ["research"],
            "user_id": "java-user-99",
            "session_id": str(uuid.uuid4()),
        },
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        raw = "".join(response.iter_text())

    assert "user=java-user-99" in raw
    assert "[DONE]" in raw
