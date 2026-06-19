"""Tests for platform SessionService."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from legal_ai_platform.models.agent import AgentRequest, AgentResponse, PolicyInput
from legal_ai_platform.session import SessionFileStore, SessionService


@pytest.fixture
def session_service() -> SessionService:
    tmp = Path(tempfile.mkdtemp())
    return SessionService(SessionFileStore(tmp))


def test_load_or_create_new_session(session_service: SessionService):
    state = session_service.load_or_create("thread-1", "tenant-a")
    assert state.thread_id == "thread-1"
    assert state.tenant_id == "tenant-a"
    assert state.turns == []


def test_persist_and_reload(session_service: SessionService):
    state = session_service.load_or_create("thread-1", "tenant-a")
    session_service.append_user_turn(state, "Hello")
    session_service.persist(state)

    reloaded = session_service.load_or_create("thread-1", "tenant-a")
    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].content == "Hello"


def test_capture_matter_from_request(session_service: SessionService):
    state = session_service.load_or_create("t1", "demo")
    request = AgentRequest(
        query="review",
        contract_text="Contract body",
        contract_document_id="550e8400-e29b-41d4-a716-446655440000",
        policies=[PolicyInput(title="P", text="Policy body")],
        contract_title="MSA",
    )
    session_service.capture_matter_from_request(state, request)
    assert state.matter.contract_text == "Contract body"
    assert state.matter.contract_document_id == "550e8400-e29b-41d4-a716-446655440000"
    assert len(state.matter.policies) == 1


def test_merge_matter_contract_document_id(session_service: SessionService):
    state = session_service.load_or_create("t1", "demo")
    state.matter.contract_document_id = "550e8400-e29b-41d4-a716-446655440000"

    request = AgentRequest(query="follow up")
    enriched = session_service.enrich_request(request, state)
    assert enriched.contract_document_id == "550e8400-e29b-41d4-a716-446655440000"


def test_merge_matter_into_request(session_service: SessionService):
    state = session_service.load_or_create("t1", "demo")
    state.matter.contract_text = "Stored contract"
    state.matter.policies = [{"title": "P", "text": "Stored policy"}]

    request = AgentRequest(query="follow up on liability")
    enriched = session_service.enrich_request(request, state)
    assert enriched.contract_text == "Stored contract"
    assert enriched.policies is not None
    assert enriched.context["session"]["thread_id"] == "t1"


def test_assistant_turn_updates_matter_agent(session_service: SessionService):
    state = session_service.load_or_create("t1", "demo")
    response = AgentResponse(
        agent="review",
        task_type="review",
        output="Report markdown",
        artifacts={"report": {"findings": []}},
        success=True,
    )
    session_service.append_assistant_turn(
        state, content=response.output, agent="review", task_type="review"
    )
    session_service.capture_matter_from_response(state, response)
    assert state.matter.last_agent == "review"
    assert state.matter.last_review_report == {"findings": []}
