"""Fail-fast dependency checks before review graph execution."""

from __future__ import annotations

import os

import httpx
from document_core.schemas.chunk import SearchRequest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings

STALE_MCP_MESSAGE = (
    "document-mcp does not support SearchRequest.metadata — likely a stale process on "
    "port 8003. Run: Legal ai/scripts/stop_document_mcp.ps1 then "
    "start_document_mcp.ps1 -Replace"
)


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


async def check_mcp_search_metadata_capability(
    client: DocumentMCPClient,
    *,
    tenant_id: str = "e2e-demo",
) -> None:
    """Probe P0-1 surface: search_policy_by_categories with metadata.categories."""
    health = await client.health()
    capabilities = list(health.get("capabilities") or [])
    if "search_request_metadata" not in capabilities:
        raise ReviewPreflightError(STALE_MCP_MESSAGE)

    request = SearchRequest(tenant_id=tenant_id, query="preflight-probe", top_k=1)
    payload = request.model_dump(mode="json")
    payload["metadata"] = {**(payload.get("metadata") or {}), "categories": []}

    url = f"{client.base_url}/tools/search_policy_by_categories"
    try:
        if client._injected_client is not None:
            response = await client._injected_client.post(url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=client.timeout_seconds) as http:
                response = await http.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001
        raise ReviewPreflightError(f"document-mcp category search probe failed: {exc}") from exc

    if response.status_code >= 400:
        body = (response.text or "").lower()
        if "metadata" in body:
            raise ReviewPreflightError(STALE_MCP_MESSAGE)
        raise ReviewPreflightError(
            f"document-mcp category search probe failed ({response.status_code}): {response.text[:300]}"
        )


async def run_review_preflight(
    client: DocumentMCPClient,
    *,
    preflight_enabled: bool = True,
    mcp_capability_probe: bool | None = None,
) -> None:
    if not preflight_enabled:
        return
    check_llm_credentials()
    await check_document_mcp(client)
    probe = (
        mcp_capability_probe
        if mcp_capability_probe is not None
        else get_settings().review_preflight_mcp_capability_probe
    )
    if probe:
        await check_mcp_search_metadata_capability(client)
