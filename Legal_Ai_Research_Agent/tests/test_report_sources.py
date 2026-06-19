"""Tests for memorandum source post-processing."""

from deep_research_from_scratch.report_sources import (
    build_case_digest,
    ensure_sources_section,
    linkify_citations,
)
from deep_research_from_scratch.research_agent_normal import _append_sources_section
from deep_research_from_scratch.source_registry import RetrievedSource


def _sample_source() -> RetrievedSource:
    return RetrievedSource(
        url="https://indiankanoon.org/doc/999/",
        title="Alpha v Beta",
        fetched=True,
        source_type="indiankanoon",
    )


def test_build_case_digest_includes_full_urls():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/123456/",
            title="Test v State",
            fetched=True,
            excerpt="The court held that indefinite freeze is impermissible.",
        )
    ]
    digest = build_case_digest(sources)
    assert "https://indiankanoon.org/doc/123456/" in digest
    assert "indefinite freeze" in digest


def test_ensure_sources_section_appends_full_urls():
    report = "## Discussion\nSome analysis.\n\n## Disclaimer\nNot legal advice."
    sources = [_sample_source()]
    out = ensure_sources_section(report, sources)
    assert "### Sources" in out
    assert "[Indian Kanoon:1] [Alpha v Beta](https://indiankanoon.org/doc/999/)" in out


def test_ensure_sources_section_strips_table_of_authorities():
    report = (
        "## Discussion\nHolding [Indian Kanoon:1].\n\n"
        "## Table of Authorities\n\n"
        "[Indian Kanoon:1] Alpha v Beta — stale footer\n\n"
        "## Disclaimer\nNot legal advice."
    )
    out = ensure_sources_section(report, [_sample_source()])
    assert "## Table of Authorities" not in out
    assert "### Sources" in out
    assert "[Indian Kanoon:1] [Alpha v Beta](https://indiankanoon.org/doc/999/)" in out


def test_deep_finalize_sequence_linkifies_inline_tokens():
    report = "## Discussion\nThe court held X [Indian Kanoon:1]."
    sources = [_sample_source()]
    out = linkify_citations(ensure_sources_section(report, sources), sources)
    assert "[Indian Kanoon:1](https://indiankanoon.org/doc/999/)" in out
    assert "### Sources" in out


def test_normal_post_processing_linkifies_body_and_footer():
    report = "## Brief Answer\nSection 103 applies [Indian Kanoon:1]."
    sources = [_sample_source()]
    out = linkify_citations(_append_sources_section(report, sources), sources)
    assert "[Indian Kanoon:1](https://indiankanoon.org/doc/999/)" in out
    assert "## Table of Authorities" in out
