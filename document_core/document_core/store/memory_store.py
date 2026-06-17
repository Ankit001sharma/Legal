"""Document store protocol and in-memory implementation (pgvector later)."""

from __future__ import annotations

import threading
from typing import Protocol
from uuid import UUID

from document_core.schemas.chunk import DocumentKind, DocumentTree, IndexedChunk


class DocumentStore(Protocol):
    def save_document(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None: ...

    def get_parents(
        self,
        tenant_id: str,
        document_id: UUID,
    ) -> list[IndexedChunk]: ...

    def get_children(
        self,
        tenant_id: str,
        document_id: UUID,
    ) -> list[IndexedChunk]: ...

    def get_canonical_text(self, tenant_id: str, document_id: UUID) -> str | None: ...

    def list_documents(
        self,
        tenant_id: str,
        kind: DocumentKind | None = None,
    ) -> list[UUID]: ...

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id: UUID,
        section_id: str,
    ) -> IndexedChunk | None: ...


class InMemoryDocumentStore:
    """Thread-safe in-memory store for development and tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._parents: dict[tuple[str, UUID], list[IndexedChunk]] = {}
        self._children: dict[tuple[str, UUID], list[IndexedChunk]] = {}
        self._canonical: dict[tuple[str, UUID], str] = {}
        self._kinds: dict[tuple[str, UUID], DocumentKind] = {}

    def save_document(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None:
        key = (parents[0].tenant_id if parents else children[0].tenant_id, tree.document_id)
        with self._lock:
            self._parents[key] = list(parents)
            self._children[key] = list(children)
            self._canonical[key] = tree.canonical_text
            if parents:
                self._kinds[key] = parents[0].kind
            elif children:
                self._kinds[key] = children[0].kind

    def get_parents(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        with self._lock:
            return list(self._parents.get((tenant_id, document_id), []))

    def get_children(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        with self._lock:
            return list(self._children.get((tenant_id, document_id), []))

    def get_canonical_text(self, tenant_id: str, document_id: UUID) -> str | None:
        with self._lock:
            return self._canonical.get((tenant_id, document_id))

    def list_documents(
        self,
        tenant_id: str,
        kind: DocumentKind | None = None,
    ) -> list[UUID]:
        with self._lock:
            doc_ids: list[UUID] = []
            for (tid, doc_id), doc_kind in self._kinds.items():
                if tid != tenant_id:
                    continue
                if kind is not None and doc_kind != kind:
                    continue
                doc_ids.append(doc_id)
            return doc_ids

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id: UUID,
        section_id: str,
    ) -> IndexedChunk | None:
        for parent in self.get_parents(tenant_id, document_id):
            if parent.section_id == section_id:
                return parent
        return None


# Process-wide default store (replaced by pgvector store in production)
_default_store = InMemoryDocumentStore()


def get_store() -> InMemoryDocumentStore:
    return _default_store


def set_store(store: InMemoryDocumentStore) -> None:
    global _default_store  # noqa: PLW0603
    _default_store = store
