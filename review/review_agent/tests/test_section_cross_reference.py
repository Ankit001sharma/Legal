"""Tests for cross-section survival resolver (Phase 22 P8)."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from review_agent.services.section_cross_reference import (
    build_classification_context,
    format_compare_related_block,
    resolve_related_sections,
)


def _section(section_id: str, title: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


def test_survival_resolver_acme_section5():
    sections = [
        _section(
            "4",
            "Protection and Use of Confidential Information",
            "During the term and for five (5) years thereafter, each Receiving Party shall protect Confidential Information.",
        ),
        _section(
            "5",
            "Term and Termination",
            "This Agreement continues for three (3) years unless terminated. Sections 3 through 10 survive termination or expiration.",
        ),
    ]
    bundle = resolve_related_sections(sections[1], sections)
    related_ids = [sid for sid, _, _ in bundle.related]
    assert "4" in related_ids
    assert bundle.resolution_reason.startswith("survival_")

    context = build_classification_context(bundle)
    assert "five (5) years" in context

    block = format_compare_related_block({"5": bundle})
    assert "five (5) years" in block
    assert "Related contract sections" in block
