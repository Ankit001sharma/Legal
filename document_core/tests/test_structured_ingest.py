"""Structured sections ingest (P2.1)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.parser.structured_sections import sections_to_tree
from document_core.schemas.chunk import (
    DocumentKind,
    IngestRequest,
    IngestSectionInput,
    ListSectionsRequest,
    StructureConfidence,
)
from document_core.services.ingest import ingest_document
from document_core.services.search import list_sections
from document_core.store.pgvector_store import PgVectorDocumentStore


def test_ingest_request_requires_text_or_sections():
    with pytest.raises(ValueError, match="text is required"):
        IngestRequest(tenant_id="t1", text="", sections=[])


def test_sections_to_tree_high_confidence_and_canonical():
    sections = [
        IngestSectionInput(section_id="12.2", title="Limitation of Liability", text="Cap is fees paid."),
        IngestSectionInput(section_id="8.1", title="Indemnification", text="Vendor shall indemnify."),
    ]
    tree = sections_to_tree(document_id=uuid4(), title="MSA", sections=sections)
    assert tree.structure_confidence == StructureConfidence.HIGH
    assert len(tree.sections) == 2
    assert tree.sections[0].section_id == "12.2"
    assert "12.2 Limitation of Liability" in tree.canonical_text
    assert "8.1 Indemnification" in tree.canonical_text


def test_sections_to_tree_duplicate_section_id_raises():
    sections = [
        IngestSectionInput(section_id="1.1", title="A", text="First."),
        IngestSectionInput(section_id="1.1", title="B", text="Duplicate."),
    ]
    with pytest.raises(ValueError, match="duplicate section_id"):
        sections_to_tree(document_id=uuid4(), title="MSA", sections=sections)


@pytest.mark.asyncio
async def test_structured_ingest_list_sections(store: PgVectorDocumentStore):
    tenant = "structured-tenant"
    sections = [
        IngestSectionInput(
            section_id="12.2",
            title="Limitation of Liability",
            text="The total liability shall not exceed fees paid in twelve months.",
        ),
        IngestSectionInput(
            section_id="8.1",
            title="Indemnification",
            text="Vendor shall indemnify Customer against third-party claims.",
        ),
    ]
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Vendor MSA",
            kind=DocumentKind.CONTRACT,
            sections=sections,
            metadata={"contract_ref": "acme-vendor-msa-2026", "source": "java-sync"},
        ),
        store=store,
    )
    assert result.structure_confidence == StructureConfidence.HIGH
    assert result.parent_count == 2
    assert any("structured sections ingest" in w for w in result.warnings)

    listed = await list_sections(
        ListSectionsRequest(tenant_id=tenant, document_id=result.document_id),
        store=store,
    )
    section_ids = {chunk.section_id for chunk in listed}
    assert section_ids == {"12.2", "8.1"}
    by_id = {chunk.section_id: chunk for chunk in listed}
    assert "twelve months" in by_id["12.2"].text.lower()
    assert "indemnify" in by_id["8.1"].text.lower()


@pytest.mark.asyncio
async def test_structured_ingest_content_hash_skip(store: PgVectorDocumentStore):
    tenant = "hash-tenant"
    document_id = uuid4()
    sections = [
        IngestSectionInput(section_id="1", title="Term", text="One year term."),
    ]
    request = IngestRequest(
        tenant_id=tenant,
        document_id=document_id,
        title="MSA",
        kind=DocumentKind.CONTRACT,
        sections=sections,
    )
    first = await ingest_document(request, store=store)
    second = await ingest_document(request, store=store)
    assert first.document_id == second.document_id
    assert second.parent_count == 1
