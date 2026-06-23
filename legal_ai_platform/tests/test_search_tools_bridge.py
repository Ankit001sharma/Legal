"""Tests that web_search routes to the Legal ai MCP provider."""

from unittest.mock import patch

from deep_research_from_scratch.search_tools import web_search


def test_web_search_uses_mcp_provider():
    with patch("deep_research_from_scratch.search_tools.run_search") as mock_run:
        mock_run.return_value = ("legal-ai results for Article 21 Constitution", [])

        result = web_search.invoke({"query": "Article 21 Constitution"})

        assert result == "legal-ai results for Article 21 Constitution"
        mock_run.assert_called_once_with("Article 21 Constitution", 5)
