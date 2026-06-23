"""Re-export the canonical MCP base client from the research agent package."""

from deep_research_from_scratch.mcp_client import BaseMCPClient, MCPClientError

__all__ = ["BaseMCPClient", "MCPClientError"]
