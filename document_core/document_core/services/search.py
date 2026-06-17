"""Search and section listing over indexed documents."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    GetSectionRequest,
    IndexedChunk,
    ListSectionsRequest,
    RetrievalHit,
    SearchRequest,
)
from document_core.search.lexical import score_query
from document_core.store.memory_store import DocumentStore, get_store


async def search_document(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    """Vector search on children; return deduplicated parent sections."""
    doc_store = store or get_store()
    doc_ids = _resolve_document_ids(doc_store, request)

    scored_children: list[tuple[float, IndexedChunk]] = []
    for doc_id in doc_ids:
        for child in doc_store.get_children(request.tenant_id, doc_id):
            if not _child_matches_filters(child, request):
                continue
            score = score_query(request.query, child.context_text or child.text)
            if score > 0:
                scored_children.append((score, child))

    scored_children.sort(key=lambda item: item[0], reverse=True)

    hits_by_parent: dict[str, RetrievalHit] = {}
    for score, child in scored_children:
        if child.parent_id is None:
            continue
        parent = _resolve_parent(doc_store, request.tenant_id, child)
        if parent is None:
            continue
        existing = hits_by_parent.get(parent.chunk_id)
        if existing:
            existing.score = max(existing.score, score)
            if child.chunk_id not in existing.matched_child_ids:
                existing.matched_child_ids.append(child.chunk_id)
        else:
            hits_by_parent[parent.chunk_id] = RetrievalHit(
                parent_chunk=parent,
                score=score,
                matched_child_ids=[child.chunk_id],
            )
        if len(hits_by_parent) >= request.top_k:
            break

    results = sorted(hits_by_parent.values(), key=lambda h: h.score, reverse=True)
    return results[: request.top_k]


async def search_contract(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    request = request.model_copy(update={"kind": DocumentKind.CONTRACT})
    return await search_document(request, store=store)


async def search_policy(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    request = request.model_copy(update={"kind": DocumentKind.POLICY})
    return await search_document(request, store=store)


async def list_sections(
    request: ListSectionsRequest,
    *,
    store: DocumentStore | None = None,
) -> list[IndexedChunk]:
    doc_store = store or get_store()
    parents: list[IndexedChunk] = []
    for parent in doc_store.get_parents(request.tenant_id, request.document_id):
        if request.kind and parent.kind != request.kind:
            continue
        parents.append(parent)
    return parents


async def get_section(
    request: GetSectionRequest,
    *,
    store: DocumentStore | None = None,
) -> IndexedChunk | None:
    doc_store = store or get_store()
    return doc_store.get_parent_by_section(
        request.tenant_id,
        request.document_id,
        request.section_id,
    )


def _resolve_document_ids(store: DocumentStore, request: SearchRequest) -> list[UUID]:
    if request.document_id:
        return [request.document_id]
    return store.list_documents(request.tenant_id, request.kind)


def _child_matches_filters(child: IndexedChunk, request: SearchRequest) -> bool:
    if request.kind and child.kind != request.kind:
        return False
    if request.policy_type and child.policy_type != request.policy_type:
        return False
    if request.contract_type and request.contract_type not in child.applies_to_contract_types:
        if child.applies_to_contract_types:
            return False
    return True


def _resolve_parent(
    store: DocumentStore,
    tenant_id: str,
    child: IndexedChunk,
) -> IndexedChunk | None:
    for parent in store.get_parents(tenant_id, child.document_id):
        if parent.chunk_id == child.parent_id:
            return parent
    return store.get_parent_by_section(tenant_id, child.document_id, child.section_id)
