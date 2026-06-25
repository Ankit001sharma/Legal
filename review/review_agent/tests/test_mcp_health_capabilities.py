"""Youngser P3 MCP health smoke."""

from __future__ import annotations


def test_mcp_capabilities_include_search_request_metadata():
    from mcp.document_server.config import MCP_CAPABILITIES
    from mcp.document_server.main import HealthResponse

    assert "search_request_metadata" in MCP_CAPABILITIES
    health = HealthResponse(
        status="ok",
        service="document-mcp",
        version="0.1.0",
        build_id="test",
        capabilities=list(MCP_CAPABILITIES),
    )
    assert "search_request_metadata" in health.capabilities
