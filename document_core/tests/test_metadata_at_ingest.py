"""Tests for ingest-time category inference."""

from __future__ import annotations

from document_core.services.metadata_at_ingest import resolve_ingest_categories


def test_resolve_ingest_categories_liability_title():
    categories, extra = resolve_ingest_categories(
        title="Limitation of Liability",
        section_texts=[],
        provided=[],
        metadata={},
    )
    assert "liability" in categories
    assert extra.get("auto_tagged") is True


def test_resolve_ingest_categories_explicit_wins():
    categories, extra = resolve_ingest_categories(
        title="Limitation of Liability",
        section_texts=[],
        provided=["privacy"],
        metadata={},
    )
    assert categories == ["privacy"]
    assert extra == {}


def test_resolve_ingest_categories_metadata_wins():
    categories, extra = resolve_ingest_categories(
        title="Limitation of Liability",
        section_texts=[],
        provided=[],
        metadata={"categories": ["indemnity"]},
    )
    assert categories == ["indemnity"]
    assert extra == {}
