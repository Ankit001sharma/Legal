"""Document MCP client HTTP pooling and retry tests."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from document_core.schemas.chunk import DocumentKind, GetSectionRequest, SearchRequest
from review_agent.clients.document_client import DocumentMCPClient


@pytest.mark.asyncio
async def test_post_reuses_shared_client() -> None:
    calls: list[str] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": []}

    class _TrackingClient:
        async def request(self, method: str, url: str, **kwargs) -> _FakeResponse:
            calls.append(f"{method}:{url}")
            return _FakeResponse()

    tracking = _TrackingClient()
    client = DocumentMCPClient("http://mcp.test", http_client=tracking)  # type: ignore[arg-type]
    request = SearchRequest(tenant_id="t1", query="indemnity", kind=DocumentKind.POLICY)
    await client.search_policy(request)
    await client.search_policy(request)
    assert len(calls) == 2
    assert all(call.startswith("POST:") for call in calls)


@pytest.mark.asyncio
async def test_persistent_client_uses_single_async_client() -> None:
    client = DocumentMCPClient("http://mcp.test")
    try:
        assert client._owns_client is True
        first_id = id(client._client)
        second = DocumentMCPClient("http://other.test")
        assert id(second._client) != first_id
        await second.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_section_404_returns_none() -> None:
    class _FakeResponse:
        status_code = 404

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("missing", request=httpx.Request("POST", "http://x"), response=self)  # type: ignore[arg-type]

        def json(self) -> dict:
            return {}

    class _FakeHttp:
        async def request(self, method: str, url: str, **kwargs) -> _FakeResponse:
            return _FakeResponse()

    client = DocumentMCPClient("http://mcp.test", http_client=_FakeHttp())  # type: ignore[arg-type]
    result = await client.get_section(
        GetSectionRequest(
            tenant_id="t1",
            document_id=uuid4(),
            section_id="99",
            kind=DocumentKind.CONTRACT,
        )
    )
    assert result is None


@pytest.mark.asyncio
async def test_post_retries_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": []}

    class _FlakyClient:
        async def request(self, method: str, url: str, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("connection refused")
            return _FakeResponse()

    async def _noop_wait_healthy(self, max_wait: float = 15.0) -> None:
        return None

    monkeypatch.setattr(DocumentMCPClient, "_wait_healthy", _noop_wait_healthy)
    client = DocumentMCPClient("http://mcp.test", http_client=_FlakyClient(), max_retries=2)  # type: ignore[arg-type]
    request = SearchRequest(tenant_id="t1", query="q", kind=DocumentKind.POLICY)
    hits = await client.search_policy(request)
    assert hits == []
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_probe_search_metadata_capability_stale_capability() -> None:
    class _HealthResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"status": "ok", "db": "ok", "capabilities": []}

    class _FakeHttp:
        async def request(self, method: str, url: str, **kwargs):
            return _HealthResponse()

    client = DocumentMCPClient("http://mcp.test", http_client=_FakeHttp())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="stale process"):
        await client.probe_search_metadata_capability()
