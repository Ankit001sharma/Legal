"""Fail-fast dependency checks before review graph execution."""

from __future__ import annotations

import os

from review_agent.clients.document_client import DocumentMCPClient


class ReviewPreflightError(RuntimeError):
    """Review cannot start — dependency unavailable."""


def _env(name: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else ""


def check_llm_credentials() -> None:
    api_key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY") or _env("MISTRAL_API_KEY")
    if api_key or _env("LLM_BASE_URL"):
        return
    raise ReviewPreflightError("LLM credentials not configured")


async def check_document_mcp(client: DocumentMCPClient) -> None:
    data = await client.health()
    if data.get("status") != "ok":
        raise ReviewPreflightError(f"document-mcp unhealthy: {data}")
    if data.get("db") != "ok":
        raise ReviewPreflightError("document-mcp Postgres ping failed")


async def run_review_preflight(
    client: DocumentMCPClient,
    *,
    preflight_enabled: bool = True,
) -> None:
    if not preflight_enabled:
        return
    check_llm_credentials()
    await check_document_mcp(client)
