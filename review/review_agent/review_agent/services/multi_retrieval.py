"""High-recall multi-path policy retrieval per contract section."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from document_core.config import get_settings as get_core_settings
from document_core.schemas.chunk import DocumentKind, IndexedChunk, RetrievalHit, SearchRequest
from document_core.schemas.taxonomy import normalize_categories
from document_core.search.reranker import rerank_hits
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_classifier import classify_section_policies

logger = logging.getLogger(__name__)


def _union_hits(
    *hit_lists: list[RetrievalHit],
    paths: dict[str, int],
) -> list[RetrievalHit]:
    merged: dict[str, RetrievalHit] = {}
    for hits in hit_lists:
        for hit in hits:
            key = hit.parent_chunk.chunk_id
            existing = merged.get(key)
            if existing is None or hit.score > existing.score:
                merged[key] = hit
            elif hit.score == existing.score:
                for cid in hit.matched_child_ids:
                    if cid not in existing.matched_child_ids:
                        existing.matched_child_ids.append(cid)
    ordered = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    paths["union_count"] = len(ordered)
    return ordered


def _parse_scope_ids(scope_document_ids: list[str] | None) -> set[str]:
    return {str(item).strip() for item in (scope_document_ids or []) if str(item).strip()}


def _is_general_only(categories: list[str]) -> bool:
    normalized = normalize_categories(categories)
    return not normalized or normalized == ["general"]


def _query_for_attempt(
    classification: SectionCategoryResult,
    section: IndexedChunk,
    attempt: int,
) -> tuple[str, list[str], bool]:
    terms = classification.query_terms or []
    title = (section.title or section.section_id or "").strip()

    if attempt == 0:
        query = terms[0] if terms else title
        return query, list(classification.categories), True
    if attempt == 1:
        if len(terms) > 1:
            return terms[1], list(classification.categories), True
        if title:
            return title, list(classification.categories), True
        fallback = terms[0] if terms else title
        return " ".join(fallback.split()[:3]), list(classification.categories), True

    query = title or (terms[0] if terms else (section.section_id or ""))
    categories = list(classification.categories)
    if "general" not in categories:
        categories.append("general")
    return query, categories, False


async def _resolve_filter_document_ids(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    categories: list[str],
    contract_type: str | None,
    scope_document_ids: list[str] | None,
    category_hard_filter: bool,
    cfg: ReviewSettings,
) -> tuple[list[UUID] | None, dict[str, Any]]:
    filter_meta: dict[str, Any] = {"category_hard_filter": category_hard_filter}
    scope_set = _parse_scope_ids(scope_document_ids)
    if scope_set:
        filter_meta["scope_document_ids"] = sorted(scope_set)

    category_ids: list[UUID] = []
    if category_hard_filter and categories:
        category_ids = await client.list_policy_ids_by_categories(
            tenant_id,
            categories,
            contract_type=contract_type,
        )
        filter_meta["category_filter_document_ids"] = [str(doc_id) for doc_id in category_ids]

    if category_hard_filter and categories and not category_ids:
        if cfg.retrieval_category_filter_fallback:
            filter_meta["category_filter_skipped"] = "no category matches"
            if scope_set:
                return [UUID(doc_id) for doc_id in scope_set], filter_meta
            return None, filter_meta
        return [], filter_meta

    doc_ids = list(category_ids)
    if scope_set:
        if doc_ids:
            doc_ids = [doc_id for doc_id in doc_ids if str(doc_id) in scope_set]
            if not doc_ids and cfg.retrieval_category_filter_fallback:
                filter_meta["category_filter_skipped"] = "scope intersection empty"
                doc_ids = [UUID(doc_id) for doc_id in scope_set]
        else:
            doc_ids = [UUID(doc_id) for doc_id in scope_set]

    return doc_ids or None, filter_meta


async def _retrieve_attempt(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    query: str,
    categories: list[str],
    contract_type: str | None,
    policy_type: str | None,
    filter_doc_ids: list[UUID] | None,
    category_hard_filter: bool,
    attempt_index: int,
    cfg: ReviewSettings,
    core,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    recall_k = cfg.retrieval_recall_top_k
    if attempt_index == 1 and cfg.retrieval_broaden_on_retry:
        recall_k = min(int(recall_k * 1.5), 50)

    request_kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "query": query,
        "kind": DocumentKind.POLICY,
        "contract_type": contract_type,
        "policy_type": policy_type,
        "top_k": recall_k,
    }
    if filter_doc_ids is not None:
        request_kwargs["document_ids"] = filter_doc_ids
    base = SearchRequest(**request_kwargs)

    step: dict[str, Any] = {
        "attempt": attempt_index,
        "query": query,
        "category_hard_filter": category_hard_filter,
        "filter_document_count": len(filter_doc_ids or []),
    }

    async def dense_path() -> list[RetrievalHit]:
        hits = await client.search_policy_recall(base)
        step["dense_count"] = len(hits)
        return hits

    async def fts_path() -> list[RetrievalHit]:
        hits = await client.search_policy_fts(base)
        step["fts_count"] = len(hits)
        return hits

    async def meta_path() -> list[RetrievalHit]:
        if not categories:
            step["metadata_count"] = 0
            return []
        hits = await client.search_policy_by_categories(base, categories=categories)
        step["metadata_count"] = len(hits)
        return hits

    dense_hits, fts_hits, meta_hits = await asyncio.gather(
        dense_path(),
        fts_path(),
        meta_path(),
    )
    union = _union_hits(dense_hits, fts_hits, meta_hits, paths=step)
    rerank_usage: dict[str, str] = {}
    reranked = rerank_hits(
        query,
        union,
        top_k=cfg.retrieval_final_top_k,
        enabled=core.reranker_enabled,
        backend=core.reranker_backend,
        max_passage_chars=core.reranker_max_passage_chars,
        fusion_retrieval_weight=core.reranker_fusion_retrieval_weight,
        usage=rerank_usage,
    )
    step["reranker_backend"] = core.reranker_backend if core.reranker_enabled else "off"
    if rerank_usage.get("reranker_used"):
        step["reranker_used"] = rerank_usage["reranker_used"]
    step["final_count"] = len(reranked)
    return reranked, step


async def multi_retrieve_for_section(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    section: IndexedChunk,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings | None = None,
    classification: SectionCategoryResult | None = None,
    scope_document_ids: list[str] | None = None,
) -> SectionRetrievalBundle:
    """Dense + FTS + metadata retrieval with retry ladder and category filtering."""
    cfg = settings or get_settings()
    core = get_core_settings()

    if classification is None:
        classification = await classify_section_policies(
            section,
            contract_type=contract_type,
            settings=cfg,
        )

    attempts_meta: list[dict[str, Any]] = []
    hits: list[RetrievalHit] = []
    winning_step: dict[str, Any] = {}
    filter_meta: dict[str, Any] = {}
    max_attempts = max(1, cfg.retrieval_max_attempts)

    for attempt_index in range(max_attempts):
        query, categories, wants_category_filter = _query_for_attempt(
            classification,
            section,
            attempt_index,
        )
        use_category_filter = wants_category_filter and cfg.retrieval_category_hard_filter
        if cfg.retrieval_skip_hard_filter_for_general and _is_general_only(
            classification.categories
        ):
            use_category_filter = False
        filter_doc_ids, resolve_meta = await _resolve_filter_document_ids(
            client,
            tenant_id=tenant_id,
            categories=categories if use_category_filter else [],
            contract_type=contract_type,
            scope_document_ids=scope_document_ids,
            category_hard_filter=use_category_filter,
            cfg=cfg,
        )
        if attempt_index == 0:
            filter_meta = resolve_meta

        if use_category_filter and filter_doc_ids is not None and not filter_doc_ids:
            step = {
                "attempt": attempt_index,
                "query": query,
                "category_hard_filter": True,
                "filter_document_count": 0,
                "dense_count": 0,
                "fts_count": 0,
                "metadata_count": 0,
                "union_count": 0,
                "final_count": 0,
            }
            attempts_meta.append(step)
            winning_step = step
            continue

        hits, step = await _retrieve_attempt(
            client,
            tenant_id=tenant_id,
            query=query,
            categories=categories,
            contract_type=contract_type,
            policy_type=policy_type,
            filter_doc_ids=filter_doc_ids,
            category_hard_filter=use_category_filter,
            attempt_index=attempt_index,
            cfg=cfg,
            core=core,
        )
        attempts_meta.append(step)
        winning_step = step
        if step["final_count"] > 0:
            break

    paths: dict[str, Any] = {
        "categories": classification.categories,
        "query_terms": classification.query_terms,
        **filter_meta,
        "attempts": attempts_meta,
        "final_attempt": winning_step.get("attempt", 0),
        "final_count": len(hits),
        "dense_count": winning_step.get("dense_count", 0),
        "fts_count": winning_step.get("fts_count", 0),
        "metadata_count": winning_step.get("metadata_count", 0),
        "union_count": winning_step.get("union_count", 0),
    }
    if winning_step.get("reranker_used"):
        paths["reranker_used"] = winning_step["reranker_used"]
    if winning_step.get("reranker_backend"):
        paths["reranker_backend"] = winning_step["reranker_backend"]
    if classification.classify_warning:
        paths["classify_warning"] = classification.classify_warning

    return SectionRetrievalBundle(
        section_id=section.section_id,
        categories=classification.categories,
        policy_hits=hits,
        retrieval_meta=paths,
    )
