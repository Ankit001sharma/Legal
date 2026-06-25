"""Search and section listing over indexed documents."""

from __future__ import annotations

import asyncio
from uuid import UUID

from document_core.config import get_settings
from document_core.schemas.chunk import (
    DocumentKind,
    GetSectionRequest,
    IndexedChunk,
    ListSectionsRequest,
    RetrievalHit,
    SearchRequest,
)
from document_core.schemas.taxonomy import normalize_categories
from document_core.search.lexical import score_query
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


async def _to_thread_if_sync(fn, *args, **kwargs):
    """Call *fn* via asyncio.to_thread if it is synchronous, else await it."""
    result = fn(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    # Already a concrete value — came from sync store (or adapter sync path)
    return result


async def search_document(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    """Search child chunks; return deduplicated parent sections."""
    doc_store = store or get_store()
    doc_ids = _resolve_document_ids(doc_store, request)
    settings = get_settings()

    scored_children: list[tuple[float, IndexedChunk]] = []
    if hasattr(doc_store, "search_children_scored_async"):
        use_hybrid = settings.search_backend == "hybrid"
        scored_children = await doc_store.search_children_scored_async(
            request,
            doc_ids,
            use_hybrid=use_hybrid,
        )
    elif hasattr(doc_store, "search_children_scored"):
        use_hybrid = settings.search_backend == "hybrid"
        scored_children = await asyncio.to_thread(
            doc_store.search_children_scored,  # type: ignore[union-attr]
            request,
            doc_ids,
            use_hybrid=use_hybrid,
        )
    else:
        for doc_id in doc_ids:
            if hasattr(doc_store, "get_children_async"):
                children_list = await doc_store.get_children_async(request.tenant_id, doc_id)
            else:
                children_list = await asyncio.to_thread(doc_store.get_children, request.tenant_id, doc_id)
            for child in children_list:
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
        parent = await _resolve_parent_async(doc_store, request.tenant_id, child)
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


async def search_policy_fts(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    """FTS-only policy search (Phase 10 recall path)."""
    request = request.model_copy(update={"kind": DocumentKind.POLICY})
    return await _search_document_fts(request, store=store)


async def search_policy_recall(request: SearchRequest, *, store: DocumentStore | None = None) -> list[RetrievalHit]:
    """Hybrid policy search with recall top_k from settings."""
    settings = get_settings()
    recall_k = max(request.top_k, settings.retrieval_recall_top_k)
    request = request.model_copy(update={"kind": DocumentKind.POLICY, "top_k": recall_k})
    return await search_document(request, store=store)


async def list_policy_ids_by_categories(
    tenant_id: str,
    categories: list[str],
    *,
    contract_type: str | None = None,
    store: DocumentStore | None = None,
) -> list[UUID]:
    doc_store = store or get_store()
    if hasattr(doc_store, "list_document_ids_by_categories_async"):
        return await doc_store.list_document_ids_by_categories_async(
            tenant_id,
            categories,
            contract_type=contract_type,
            kind=DocumentKind.POLICY,
        )
    if not hasattr(doc_store, "list_document_ids_by_categories"):
        return []
    return await asyncio.to_thread(
        doc_store.list_document_ids_by_categories,  # type: ignore[union-attr]
        tenant_id,
        categories,
        contract_type=contract_type,
        kind=DocumentKind.POLICY,
    )


async def search_policy_by_categories(
    tenant_id: str,
    categories: list[str],
    query: str,
    *,
    contract_type: str | None = None,
    policy_type: str | None = None,
    top_k: int | None = None,
    store: DocumentStore | None = None,
) -> list[RetrievalHit]:
    """Metadata filter → document IDs → hybrid search within those docs."""
    settings = get_settings()
    limit = top_k or settings.retrieval_recall_top_k
    doc_ids = await list_policy_ids_by_categories(
        tenant_id,
        categories,
        contract_type=contract_type,
        store=store,
    )
    if not doc_ids:
        return []
    request = SearchRequest(
        tenant_id=tenant_id,
        query=query,
        kind=DocumentKind.POLICY,
        contract_type=contract_type,
        policy_type=policy_type,
        top_k=limit,
        document_ids=doc_ids,
    )
    hits = await search_document(request, store=store)
    return boost_parent_category_hits(
        hits,
        categories,
        boost=settings.category_search_boost,
    )


async def _search_document_fts(
    request: SearchRequest,
    *,
    store: DocumentStore | None = None,
) -> list[RetrievalHit]:
    doc_store = store or get_store()
    doc_ids = _resolve_document_ids(doc_store, request)
    if hasattr(doc_store, "search_children_fts_async"):
        scored_children = await doc_store.search_children_fts_async(request, doc_ids)
    elif hasattr(doc_store, "search_children_fts"):
        scored_children = await asyncio.to_thread(
            doc_store.search_children_fts,  # type: ignore[union-attr]
            request,
            doc_ids,
        )
    else:
        return []
    return await _hits_from_scored_async(doc_store, request, scored_children)


def _hits_from_scored(
    doc_store: DocumentStore,
    request: SearchRequest,
    scored_children: list[tuple[float, IndexedChunk]],
) -> list[RetrievalHit]:
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


async def _hits_from_scored_async(
    doc_store: DocumentStore,
    request: SearchRequest,
    scored_children: list[tuple[float, IndexedChunk]],
) -> list[RetrievalHit]:
    hits_by_parent: dict[str, RetrievalHit] = {}
    for score, child in scored_children:
        if child.parent_id is None:
            continue
        parent = await _resolve_parent_async(doc_store, request.tenant_id, child)
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


async def list_sections(
    request: ListSectionsRequest,
    *,
    store: DocumentStore | None = None,
) -> list[IndexedChunk]:
    doc_store = store or get_store()
    if hasattr(doc_store, "get_policy_registry_by_document_id_async"):
        record = await doc_store.get_policy_registry_by_document_id_async(request.tenant_id, request.document_id)
    else:
        record = await asyncio.to_thread(doc_store.get_policy_registry_by_document_id, request.tenant_id, request.document_id)
    if record is not None and record.index_status == "deleted":
        raise ValueError("document deleted")

    if hasattr(doc_store, "get_parents_async"):
        all_parents = await doc_store.get_parents_async(request.tenant_id, request.document_id)
    else:
        all_parents = await asyncio.to_thread(doc_store.get_parents, request.tenant_id, request.document_id)

    parents: list[IndexedChunk] = []
    for parent in all_parents:
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
    if hasattr(doc_store, "get_parent_by_section_async"):
        return await doc_store.get_parent_by_section_async(
            request.tenant_id,
            request.document_id,
            request.section_id,
        )
    return await asyncio.to_thread(
        doc_store.get_parent_by_section,
        request.tenant_id,
        request.document_id,
        request.section_id,
    )


def _resolve_document_ids(store: DocumentStore, request: SearchRequest) -> list[UUID]:
    if request.document_ids:
        return list(request.document_ids)
    if request.document_id:
        return [request.document_id]
    return store.list_documents(request.tenant_id, request.kind)


def _child_matches_filters(child: IndexedChunk, request: SearchRequest) -> bool:
    if request.kind and child.kind != request.kind:
        return False
    if request.policy_type and child.policy_type != request.policy_type:
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


async def _resolve_parent_async(
    store: DocumentStore,
    tenant_id: str,
    child: IndexedChunk,
) -> IndexedChunk | None:
    if hasattr(store, "get_parents_async"):
        parents = await store.get_parents_async(tenant_id, child.document_id)
    else:
        parents = await asyncio.to_thread(store.get_parents, tenant_id, child.document_id)
    for parent in parents:
        if parent.chunk_id == child.parent_id:
            return parent
    if hasattr(store, "get_parent_by_section_async"):
        return await store.get_parent_by_section_async(tenant_id, child.document_id, child.section_id)
    return await asyncio.to_thread(store.get_parent_by_section, tenant_id, child.document_id, child.section_id)


def parent_categories(hit: RetrievalHit) -> list[str]:
    raw = (hit.parent_chunk.metadata or {}).get("categories")
    return normalize_categories(raw if isinstance(raw, list) else [])


def boost_parent_category_hits(
    hits: list[RetrievalHit],
    categories: list[str],
    *,
    boost: float,
) -> list[RetrievalHit]:
    """Multiply score when parent section categories overlap query categories."""
    want = set(normalize_categories(categories))
    if not want or boost <= 0:
        return hits
    out: list[RetrievalHit] = []
    for hit in hits:
        score = hit.score
        if want.intersection(parent_categories(hit)):
            score *= 1.0 + boost
        out.append(hit.model_copy(update={"score": score}))
    return sorted(out, key=lambda h: h.score, reverse=True)


def count_parent_category_hits(hits: list[RetrievalHit], categories: list[str]) -> int:
    want = set(normalize_categories(categories))
    if not want:
        return 0
    return sum(1 for hit in hits if want.intersection(parent_categories(hit)))
