"""Tests for review preflight gates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.services.review_preflight import (
    ReviewPreflightError,
    check_llm_credentials,
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
    client.health = AsyncMock(return_value={"status": "ok", "db": "ok"})
    await run_review_preflight(client)
