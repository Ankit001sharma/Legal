"""Tests for review preflight gates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.services.review_preflight import (
    ReviewPreflightError,
    check_llm_credentials,
    check_mcp_search_metadata_capability,
    run_review_preflight,
)


def test_check_llm_credentials_missing(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    with pytest.raises(ReviewPreflightError, match="LLM credentials"):
        check_llm_credentials()


def test_check_llm_credentials_with_api_key(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    check_llm_credentials()


def test_check_llm_credentials_with_base_url(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8000/v1")
    check_llm_credentials()


@pytest.mark.asyncio
async def test_preflight_disabled_skips_checks(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    client = AsyncMock(spec=DocumentMCPClient)
    await run_review_preflight(client, preflight_enabled=False)
    client.health.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_fails_on_unhealthy_mcp(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.health = AsyncMock(return_value={"status": "degraded", "db": "error"})
    with pytest.raises(ReviewPreflightError, match="unhealthy"):
        await run_review_preflight(client)


@pytest.mark.asyncio
async def test_preflight_passes_when_healthy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.base_url = "http://localhost:8003"
    client.timeout_seconds = 5.0
    client._injected_client = None
    client.health = AsyncMock(
        return_value={
            "status": "ok",
            "db": "ok",
            "capabilities": ["search_request_metadata"],
        }
    )
    monkeypatch.setattr(
        "review_agent.services.review_preflight.check_mcp_search_metadata_capability",
        AsyncMock(),
    )
    await run_review_preflight(client)


@pytest.mark.asyncio
async def test_preflight_rejects_stale_mcp_metadata_error(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.base_url = "http://localhost:8003"
    client.timeout_seconds = 5.0
    client.health = AsyncMock(
        return_value={"status": "ok", "db": "ok", "capabilities": []},
    )
    with pytest.raises(ReviewPreflightError, match="stale process"):
        await check_mcp_search_metadata_capability(client)


@pytest.mark.asyncio
async def test_preflight_probe_http_500_metadata(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.base_url = "http://localhost:8003"
    client.timeout_seconds = 5.0
    client.health = AsyncMock(
        return_value={
            "status": "ok",
            "db": "ok",
            "capabilities": ["search_request_metadata"],
        },
    )

    class _FakeResponse:
        status_code = 500
        text = "'SearchRequest' object has no attribute 'metadata'"

    fake_http = AsyncMock()
    fake_http.post = AsyncMock(return_value=_FakeResponse())
    client._injected_client = fake_http

    with pytest.raises(ReviewPreflightError, match="stale process"):
        await check_mcp_search_metadata_capability(client)
