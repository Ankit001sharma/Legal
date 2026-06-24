"""Tests for parent/child chunking (Phase 37D)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.config import DocumentCoreSettings
from document_core.indexer.parent_child import (
    CHUNK_VERSION,
    _sentences_from_text,
    _split_child_chunks,
    build_parent_child_chunks,
)
from document_core.schemas.chunk import (
    DocumentKind,
    DocumentTree,
    IngestRequest,
    SectionNode,
    StructureConfidence,
)
from document_core.services.ingest import ingest_document
from document_core.store.content_hash import content_hash, metadata_fingerprint

_LIABILITY_SENTENCE = (
    "The total liability of Vendor under this Agreement shall not exceed "
    "the fees paid in the twelve (12) months preceding the claim, "
    "including all direct and consequential damages caps."
)
LONG_LIABILITY = " ".join([_LIABILITY_SENTENCE] * 10)


def _liability_tree() -> DocumentTree:
    return DocumentTree(
        document_id=uuid4(),
        title="Liability Policy",
        canonical_text=LONG_LIABILITY,
        sections=[
            SectionNode(
                section_id="12.2",
                section_path="12.2",
                title="Limitation of Liability",
                level=1,
                text=LONG_LIABILITY,
            )
        ],
        structure_confidence=StructureConfidence.HIGH,
    )


def _build(tree: DocumentTree, *, max_chars: int = 700, overlap: int = 2):
    settings = DocumentCoreSettings(
        child_chunk_max_chars=max_chars,
        child_chunk_overlap_sentences=overlap,
    )
    return build_parent_child_chunks(
        tree=tree,
        tenant_id="test",
        kind=DocumentKind.POLICY,
        metadata={"chunk_version": CHUNK_VERSION},
        settings=settings,
    )


def test_long_liability_splits_into_multiple_children():
    parents, children, skipped = _build(_liability_tree())
    assert len(parents) == 1
    assert skipped == 0
    assert len(children) >= 2


def test_chunks_respect_max_size():
    max_chars = 700
    _, children, _ = _build(_liability_tree(), max_chars=max_chars)
    for child in children:
        assert len(child.text) <= max_chars + 50


def test_sentences_preserved_in_chunks():
    sentences = _sentences_from_text(LONG_LIABILITY)
    chunks = _split_child_chunks(LONG_LIABILITY, max_chars=400)
    assert len(chunks) >= 2
    rejoined = " ".join(chunks)
    for sent in sentences:
        assert sent in rejoined


def test_overlap_in_context_not_text():
    tree = _liability_tree()
    chunks = _split_child_chunks(tree.sections[0].text, max_chars=400)
    _, children, _ = _build(tree, max_chars=400, overlap=2)
    assert len(children) >= 2
    assert children[1].text == chunks[1]
    tail = " ".join(_sentences_from_text(chunks[0])[-2:])
    if tail:
        assert tail in children[1].context_text
        assert len(children[1].context_text) > len(children[1].text)


def test_empty_section_skipped():
    tree = DocumentTree(
        document_id=uuid4(),
        title="Mixed",
        canonical_text="body",
        sections=[
            SectionNode(
                section_id="1",
                section_path="1",
                title="Empty",
                level=1,
                text="   ",
            ),
            SectionNode(
                section_id="2",
                section_path="2",
                title="Filled",
                level=1,
                text="Vendor liability shall not exceed fees paid.",
            ),
        ],
        structure_confidence=StructureConfidence.HIGH,
    )
    parents, children, skipped = _build(tree)
    assert skipped == 1
    assert len(parents) == 1
    assert parents[0].section_id == "2"
    assert children


def test_context_text_includes_path_and_title():
    _, children, _ = _build(_liability_tree())
    child = children[0]
    assert "12.2" in child.context_text
    assert "Limitation of Liability" in child.context_text


def test_chunk_version_in_content_hash():
    fp = metadata_fingerprint({"chunk_version": CHUNK_VERSION, "categories": ["liability"]})
    assert fp["chunk_version"] == CHUNK_VERSION
    h1 = content_hash("same text", {"chunk_version": 1, "categories": ["liability"]})
    h2 = content_hash("same text", {"chunk_version": CHUNK_VERSION, "categories": ["liability"]})
    assert h1 != h2


class _CapturingStore:
    def __init__(self) -> None:
        self.meta: dict = {}

    def save_document(self, *, tree, parents, children) -> None:
        self.meta = parents[0].metadata if parents else {}


@pytest.mark.asyncio
async def test_ingest_sets_chunk_version():
    cap = _CapturingStore()
    await ingest_document(
        IngestRequest(
            tenant_id="chunk-v",
            title="Policy",
            kind=DocumentKind.POLICY,
            text="Vendor liability shall not exceed fees paid.",
        ),
        store=cap,
    )
    assert cap.meta.get("chunk_version") == CHUNK_VERSION
