"""Build parent and child chunks from a section tree."""

from __future__ import annotations

import re
from uuid import UUID

from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    DocumentTree,
    IndexedChunk,
    SectionNode,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def build_parent_child_chunks(
    *,
    tree: DocumentTree,
    tenant_id: str,
    kind: DocumentKind,
    policy_type: str | None = None,
    applies_to_contract_types: list[str] | None = None,
    metadata: dict | None = None,
) -> tuple[list[IndexedChunk], list[IndexedChunk]]:
    """Return (parents, children) chunk lists."""
    parents: list[IndexedChunk] = []
    children: list[IndexedChunk] = []
    applies = applies_to_contract_types or []
    meta = metadata or {}

    def walk(nodes: list[SectionNode], breadcrumb: str = "") -> None:
        for node in nodes:
            path = f"{breadcrumb}/{node.section_id}" if breadcrumb else node.section_id
            parent_id = f"{tree.document_id}:{node.section_id}"
            parent = IndexedChunk(
                chunk_id=parent_id,
                document_id=tree.document_id,
                tenant_id=tenant_id,
                kind=kind,
                chunk_role=ChunkRole.PARENT,
                parent_id=None,
                section_id=node.section_id,
                section_path=path,
                title=node.title,
                text=node.text,
                context_text=node.text,
                policy_type=policy_type,
                applies_to_contract_types=applies,
                metadata=meta,
            )
            parents.append(parent)

            for idx, child_text in enumerate(_split_child_units(node.text)):
                child_id = f"{parent_id}:c{idx}"
                context = f"{path} > {node.title}\n{child_text}".strip()
                children.append(
                    IndexedChunk(
                        chunk_id=child_id,
                        document_id=tree.document_id,
                        tenant_id=tenant_id,
                        kind=kind,
                        chunk_role=ChunkRole.CHILD,
                        parent_id=parent_id,
                        section_id=node.section_id,
                        section_path=path,
                        title=node.title,
                        text=child_text,
                        context_text=context,
                        policy_type=policy_type,
                        applies_to_contract_types=applies,
                        metadata=meta,
                    )
                )

            walk(node.children, path)

    walk(tree.sections)
    return parents, children


def _split_child_units(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        if len(para) < 400:
            units.append(para)
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(para) if s.strip()]
        if len(sentences) <= 1:
            units.append(para)
        else:
            units.extend(sentences)
    return units or [text.strip()]
