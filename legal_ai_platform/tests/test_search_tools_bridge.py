"""Tests that web_search routes to the Legal ai MCP provider."""

from unittest.mock import patch

from deep_research_from_scratch.search_tools import web_search


def test_web_search_uses_mcp_provider():
    def fake_provider(query, max_results, topic):
        return f"legal-ai results for {query}"

    with patch("deep_research_from_scratch.search_tools._default_mcp_provider", fake_provider):
        result = web_search.invoke({"query": "Article 21 Constitution"})
        assert result == "legal-ai results for Article 21 Constitution"
