"""Phase 10 section policy retrieval graph node."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.async_limits import gather_limited
from review_agent.services.multi_retrieval import multi_retrieve_for_section
from review_agent.services.section_classifier import classify_all_sections
from review_agent.services.section_filter import filter_review_sections
from review_agent.state.review_state import ReviewState


async def section_policy_retrieval_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    sections = filter_review_sections(
        state.get("contract_sections") or [],
        min_chars=settings.review_min_section_chars,
    )
    scope_ids = list(
        state.get("policy_document_ids")
        or state.get("discovered_policy_document_ids")
        or []
    )
    classifications = await classify_all_sections(
        sections,
        contract_type=state.get("contract_type"),
        settings=settings,
    )

    coros = [
        multi_retrieve_for_section(
            client,
            tenant_id=state["tenant_id"],
            section=section,
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            settings=settings,
            classification=classifications.get(section.section_id),
            scope_document_ids=scope_ids or None,
        )
        for section in sections
    ]
    results = await gather_limited(coros, limit=settings.section_retrieval_concurrency)

    bundles: dict[str, SectionRetrievalBundle] = {}
    warnings: list[str] = []
    for section, result in zip(sections, results, strict=True):
        if isinstance(result, BaseException):
            warnings.append(f"retrieval failed for section {section.section_id}: {result}")
            bundles[section.section_id] = SectionRetrievalBundle(
                section_id=section.section_id,
                categories=["general"],
                policy_hits=[],
                retrieval_meta={"error": str(result)},
            )
            continue
        bundles[section.section_id] = result

    serialized = {k: v.model_dump(mode="json") for k, v in bundles.items()}
    path_totals = {"dense": 0, "fts": 0, "metadata": 0}
    retry_sections = 0
    zero_hit_sections = 0
    max_attempts_used = 0
    for bundle in bundles.values():
        meta = bundle.retrieval_meta or {}
        path_totals["dense"] += int(meta.get("dense_count") or 0)
        path_totals["fts"] += int(meta.get("fts_count") or 0)
        path_totals["metadata"] += int(meta.get("metadata_count") or 0)
        attempts = meta.get("attempts") or []
        if len(attempts) > 1:
            retry_sections += 1
        if not bundle.policy_hits:
            zero_hit_sections += 1
        if attempts:
            max_attempts_used = max(max_attempts_used, len(attempts))

    return {
        "section_retrieval_by_id": serialized,
        "section_review_sections": [s.model_dump(mode="json") for s in sections],
        "warnings": warnings,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            "sections_retrieved": len(bundles),
            "retrieval_path_hits": path_totals,
            "retrieval_retry_sections": retry_sections,
            "retrieval_zero_hit_sections": zero_hit_sections,
            "retrieval_max_attempts_used": max_attempts_used,
        },
    }
