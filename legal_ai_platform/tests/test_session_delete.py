"""Tests for platform session delete and read API."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from legal_ai_platform.gateway.app import app
from legal_ai_platform.models.agent import AgentRequest
from legal_ai_platform.session import SessionFileStore, SessionService


@pytest.fixture
def session_service() -> SessionService:
    tmp = Path(tempfile.mkdtemp())
    return SessionService(SessionFileStore(tmp), platform_owns_session=True)


def test_delete_session_removes_state(session_service: SessionService):
    state = session_service.load_or_create("thread-del", "tenant-a")
    session_service.append_user_turn(state, "hello")
    session_service.persist(state)
    assert session_service.get_session("tenant-a", "thread-del") is not None

    result = session_service.delete_session("tenant-a", "thread-del")
    assert result["deleted"] is True
    assert session_service.get_session("tenant-a", "thread-del") is None


def test_delete_session_cleans_legacy_research_files(session_service: SessionService, tmp_path, monkeypatch):
    legacy_root = tmp_path / "memory"
    sessions_dir = legacy_root / "sessions"
    sessions_dir.mkdir(parents=True)
    jsonl = sessions_dir / "legacy-thread.jsonl"
    jsonl.write_text('{"type":"user"}\n', encoding="utf-8")
    monkeypatch.setenv("DEEP_RESEARCH_MEMORY_DIR", str(legacy_root))

    result = session_service.delete_session("default", "legacy-thread")
    assert result["deleted"] is False
    assert not jsonl.exists()
    assert "legacy-thread.jsonl" in str(result["legacy_research_files_removed"])


def test_get_and_delete_session_api():
    tmp = Path(tempfile.mkdtemp())
    svc = SessionService(SessionFileStore(tmp), platform_owns_session=True)
    state = svc.load_or_create("api-thread", "demo")
    svc.append_user_turn(state, "test query")
    svc.persist(state)

    client = TestClient(app)

    class _Container:
        session_service = svc

    app.state.container = _Container()

    get_resp = client.get("/sessions/api-thread", params={"tenant_id": "demo"})
    assert get_resp.status_code == 200
    assert get_resp.json()["thread_id"] == "api-thread"
    assert len(get_resp.json()["turns"]) == 1

    del_resp = client.delete("/sessions/api-thread", params={"tenant_id": "demo"})
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    missing = client.get("/sessions/api-thread", params={"tenant_id": "demo"})
    assert missing.status_code == 404
