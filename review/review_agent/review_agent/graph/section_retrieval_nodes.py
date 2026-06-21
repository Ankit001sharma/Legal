"""Phase 10 section policy retrieval graph node."""

from __future__ import annotations

from typing import Any

from document_core.config import get_settings as get_core_settings
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
    core = get_core_settings()
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

    warnings: list[str] = []
    for section in sections:
        classification = classifications.get(section.section_id)
        if classification and classification.classify_warning:
            label = "classifier note"
            if classification.categories == ["general"] or "fallback" in (
                classification.classify_warning or ""
            ).lower():
                label = "classifier fallback"
            warnings.append(
                f"section {section.section_id} {label} (categories="
                f"{classification.categories}): {classification.classify_warning}"
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
    reranker_cross_encoder_sections = 0
    reranker_lexical_fallback_sections = 0
    reranker_off_sections = 0
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
        used = meta.get("reranker_used")
        if used == "cross_encoder":
            reranker_cross_encoder_sections += 1
        elif used in ("lexical_fallback", "lexical"):
            reranker_lexical_fallback_sections += 1
        elif used == "off" or meta.get("reranker_backend") == "off" or not core.reranker_enabled:
            reranker_off_sections += 1

    reranker_backend_config = core.reranker_backend if core.reranker_enabled else "off"

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
            "reranker_cross_encoder_sections": reranker_cross_encoder_sections,
            "reranker_lexical_fallback_sections": reranker_lexical_fallback_sections,
            "reranker_off_sections": reranker_off_sections,
            "reranker_backend_config": reranker_backend_config,
        },
    }
