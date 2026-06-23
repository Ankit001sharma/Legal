"""Tests for source enrichment and citation linkification."""

from unittest.mock import patch

from deep_research_from_scratch.report_sources import (
    build_procedural_timeline_digest,
    ensure_sources_section,
    linkify_citations,
)
from deep_research_from_scratch.source_enrichment import refetch_snippet_sources
from deep_research_from_scratch.source_registry import RetrievedSource


def test_linkify_citations_makes_inline_tokens_clickable():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/123/",
            title="Alpha v State",
            fetched=True,
            source_type="indiankanoon",
        )
    ]
    report = "The court held X [Indian Kanoon:1] on this point."
    out = linkify_citations(report, sources)
    assert '<a href="https://indiankanoon.org/doc/123/"' in out
    assert '[Indian Kanoon:1]</a>' in out


def test_ensure_sources_section_uses_clickable_links():
    report = "## Main Analysis\nDone."
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/999/",
            title="Beta v State",
            fetched=True,
            source_type="indiankanoon",
        )
    ]
    out = ensure_sources_section(report, sources)
    assert "[Indian Kanoon:1] [Beta v State](https://indiankanoon.org/doc/999/)" in out


def test_build_procedural_timeline_digest_lists_milestones():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Bail order",
            fetched=True,
            excerpt="The accused was granted bail after FIR was registered.",
        )
    ]
    digest = build_procedural_timeline_digest(sources, "Pune crash case")
    assert "### Fir" in digest or "### FIR" in digest.title()
    assert "### Bail" in digest


def test_refetch_snippet_sources_upgrades_registry():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/555/",
            title="Gamma v State",
            fetched=False,
            source_type="indiankanoon",
        )
    ]
    fetched = RetrievedSource(
        url="https://indiankanoon.org/doc/555/",
        title="Gamma v State",
        fetched=True,
        excerpt="Full judgment text about culpable homicide.",
        source_type="indiankanoon",
    )
    with patch("deep_research_from_scratch.source_enrichment.run_fetch") as mock_fetch:
        mock_fetch.return_value = ("fetch body", fetched)
        merged, note = refetch_snippet_sources(sources, max_refetches=5)
    assert any(s.fetched for s in merged)
    assert "Snippet refetch pass" in note
