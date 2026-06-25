"""Build parent and child chunks from a section tree."""

from __future__ import annotations

import re

from document_core.config import DocumentCoreSettings, get_settings
from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    DocumentTree,
    IndexedChunk,
    SectionNode,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
CHUNK_VERSION = 2


def build_parent_child_chunks(
    *,
    tree: DocumentTree,
    tenant_id: str,
    kind: DocumentKind,
    policy_type: str | None = None,
    metadata: dict | None = None,
    settings: DocumentCoreSettings | None = None,
) -> tuple[list[IndexedChunk], list[IndexedChunk], int]:
    """Return (parents, children, skipped_empty_sections)."""
    cfg = settings or get_settings()
    parents: list[IndexedChunk] = []
    children: list[IndexedChunk] = []
    meta = metadata or {}
    skipped_empty = 0

    def walk(nodes: list[SectionNode], breadcrumb: str = "") -> None:
        nonlocal skipped_empty
        for node in nodes:
            path = f"{breadcrumb}/{node.section_id}" if breadcrumb else node.section_id
            if not node.text.strip():
                skipped_empty += 1
                walk(node.children, path)
                continue

            parent_id = f"{tree.document_id}:{node.section_id}"
            section_meta = {**meta, "categories": list(node.categories or [])}
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
                metadata=section_meta,
            )
            parents.append(parent)

            chunks = _split_child_chunks(node.text, max_chars=cfg.child_chunk_max_chars)
            overlap_n = cfg.child_chunk_overlap_sentences

            for idx, child_text in enumerate(chunks):
                overlap_prefix = ""
                if idx > 0 and overlap_n > 0:
                    prev_sents = _sentences_from_text(chunks[idx - 1])
                    tail = prev_sents[-overlap_n:]
                    if tail:
                        overlap_prefix = " ".join(tail) + " "
                child_id = f"{parent_id}:c{idx}"
                context = f"{path} > {node.title}\n{overlap_prefix}{child_text}".strip()
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
                        metadata=section_meta,
                    )
                )

            walk(node.children, path)

    walk(tree.sections)
    return parents, children, skipped_empty


def _sentences_from_text(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    out: list[str] = []
    for para in paragraphs:
        parts = [s.strip() for s in _SENTENCE_SPLIT.split(para) if s.strip()]
        out.extend(parts if len(parts) > 1 else [para])
    return out


def _expand_oversized_sentences(sentences: list[str], *, max_chars: int) -> list[str]:
    expanded: list[str] = []
    for sent in sentences:
        if len(sent) <= max_chars:
            expanded.append(sent)
            continue
        remaining = sent
        while len(remaining) > max_chars:
            chunk = remaining[:max_chars]
            last_space = chunk.rfind(" ")
            if last_space > 0:
                expanded.append(remaining[:last_space])
                remaining = remaining[last_space + 1 :].lstrip()
            else:
                expanded.append(remaining[:max_chars])
                remaining = remaining[max_chars:]
        if remaining:
            expanded.append(remaining)
    return expanded


def _pack_chunks(sentences: list[str], *, max_chars: int) -> list[str]:
    """Greedy pack: add sentences until next would exceed max_chars."""
    if not sentences:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        add_len = len(sent) + (1 if current else 0)
        if current and current_len + add_len > max_chars:
            chunks.append(" ".join(current))
            current = [sent]
            current_len = len(sent)
        else:
            current.append(sent)
            current_len += add_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_child_chunks(text: str, *, max_chars: int = 700) -> list[str]:
    sents = _sentences_from_text(text.strip())
    if not sents:
        return []
    expanded = _expand_oversized_sentences(sents, max_chars=max_chars)
    return _pack_chunks(expanded, max_chars=max_chars)
