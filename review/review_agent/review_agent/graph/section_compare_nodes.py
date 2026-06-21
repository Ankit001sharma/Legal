"""Phase 10 section-first compare, merge, and gap verify nodes."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import IndexedChunk
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.final_verify_llm import run_final_gap_verify
from review_agent.services.section_compare_llm import compare_all_sections
from review_agent.services.playbook_context import build_playbook_hints_by_document
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections
from review_agent.services.section_merge import merge_section_findings
from review_agent.state.review_state import ReviewState


def _load_bundles(state: ReviewState) -> dict[str, SectionRetrievalBundle]:
    raw = state.get("section_retrieval_by_id") or {}
    return {
        key: SectionRetrievalBundle.model_validate(value)
        for key, value in raw.items()
    }


def _load_sections(state: ReviewState) -> list[IndexedChunk]:
    raw = state.get("section_review_sections") or []
    return [IndexedChunk.model_validate(item) for item in raw]


def _playbook_hints(state: ReviewState):
    return build_playbook_hints_by_document(
        state.get("indexed_policies"),
        policy_ref_by_document_id=state.get("policy_ref_by_document_id"),
    )


async def section_compare_llm_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    sections = _load_sections(state)
    bundles = _load_bundles(state)

    hits_by_section: dict[str, list] = {
        sid: list(bundle.policy_hits) for sid, bundle in bundles.items()
    }
    categories_by_section = {
        sid: list(bundle.categories) for sid, bundle in bundles.items()
    }
    sections_with_policy = [s for s in sections if hits_by_section.get(s.section_id)]
    playbook_hints = _playbook_hints(state)

    items, compare_warnings, batch_stats = await compare_all_sections(
        sections_with_policy,
        hits_by_section,
        contract_type=state.get("contract_type"),
        memory_context=state.get("memory_context") or "",
        settings=settings,
        playbook_hints_by_document=playbook_hints,
        categories_by_section=categories_by_section,
    )

    path_counts = {"dense": 0, "fts": 0, "metadata": 0}
    for bundle in bundles.values():
        meta = bundle.retrieval_meta or {}
        if meta.get("dense_count", 0):
            path_counts["dense"] += 1
        if meta.get("fts_count", 0):
            path_counts["fts"] += 1
        if meta.get("metadata_count", 0):
            path_counts["metadata"] += 1

    stats = {
        **dict(state.get("compliance_stats") or {}),
        "compliance_mode": "section_first",
        "sections_total": len(sections),
        "sections_with_policy": len(sections_with_policy),
        "sections_no_policy": len(sections) - len(sections_with_policy),
        "compare_items": len(items),
        "retrieval_paths_used": path_counts,
        **batch_stats,
    }
    return {
        "section_compare_items": [i.model_dump(mode="json") for i in items],
        "compliance_stats": stats,
        "warnings": compare_warnings,
    }


async def merge_section_findings_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    from review_agent.schemas.section_compare import SectionCompareItem

    bundles = _load_bundles(state)
    raw_items = state.get("section_compare_items") or []
    items = [SectionCompareItem.model_validate(i) for i in raw_items]
    merged = merge_section_findings(
        items,
        bundles,
        hints_by_document=_playbook_hints(state),
        sections_by_id={s.section_id: s for s in _load_sections(state)},
    )
    return {
        "findings": merged.findings,
        "warnings": merged.warnings,
        "gap_section_ids": merged.gap_section_ids,
        "no_policy_gap_ids": merged.no_policy_gap_ids,
        "compare_omitted_gap_ids": merged.compare_omitted_gap_ids,
        "unclear_finding_ids": merged.unclear_finding_ids,
        "unclear_recompare_finding_ids": merged.unclear_recompare_finding_ids,
        "conflict_pairs": [list(pair) for pair in merged.conflict_pairs],
    }


async def final_gap_verify_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    sections = _load_sections(state)
    sections_by_id = {s.section_id: s for s in sections}
    bundles = _load_bundles(state)

    gap_ids = list(state.get("gap_section_ids") or [])
    no_policy_ids = list(state.get("no_policy_gap_ids") or [])
    compare_omitted_ids = list(state.get("compare_omitted_gap_ids") or [])
    unclear_ids = list(state.get("unclear_finding_ids") or [])
    recompare_ids = list(state.get("unclear_recompare_finding_ids") or [])
    raw_pairs = state.get("conflict_pairs") or []
    conflict_pairs = [tuple(p) for p in raw_pairs if len(p) == 2]
    existing = list(state.get("findings") or [])

    new_findings, warnings, stats, superseded_ids = await run_final_gap_verify(
        client=client,
        tenant_id=state["tenant_id"],
        sections_by_id=sections_by_id,
        bundles=bundles,
        gap_section_ids=gap_ids,
        no_policy_gap_ids=no_policy_ids,
        compare_omitted_gap_ids=compare_omitted_ids,
        unclear_finding_ids=unclear_ids,
        unclear_recompare_finding_ids=recompare_ids,
        conflict_pairs=conflict_pairs,
        existing_findings=existing,
        contract_type=state.get("contract_type"),
        policy_type=state.get("policy_type"),
        memory_context=state.get("memory_context") or "",
        settings=settings,
    )

    superseded_set = set(superseded_ids)
    resolved_section_ids = {f.contract_section_id for f in new_findings if f.contract_section_id}
    _gap_types = frozenset({"no_policy", "compare_omitted", "coverage_backfill"})
    kept_findings = [
        f
        for f in existing
        if f.finding_id not in superseded_set
        and not (
            f.contract_section_id in resolved_section_ids
            and f.metadata.get("gap_type") in _gap_types
        )
    ]
    merged_findings = kept_findings + new_findings

    coverage_warnings: list[str] = []
    section_coverage_meta: dict[str, Any] = {}
    if settings.enforce_section_coverage:
        reviewable = sections or reviewable_sections(
            [IndexedChunk.model_validate(s) for s in (state.get("contract_sections") or [])],
            min_chars=settings.review_min_section_chars,
        )
        coverage = ensure_section_coverage(
            reviewable,
            merged_findings,
            min_chars=settings.review_min_section_chars,
            sections_by_id=sections_by_id,
            settings=settings,
        )
        merged_findings = coverage.findings
        coverage_warnings = coverage.warnings
        section_coverage_meta = {
            "reviewable_count": coverage.reviewable_count,
            "uncovered_before": coverage.uncovered_before,
            "backfill_count": coverage.backfill_count,
        }

    updated_bundles = {k: v.model_dump(mode="json") for k, v in bundles.items()}

    return {
        "findings": merged_findings,
        "section_retrieval_by_id": updated_bundles,
        "final_verify_stats": stats,
        "section_coverage": section_coverage_meta,
        "superseded_finding_ids": list(dict.fromkeys(superseded_ids)),
        "warnings": warnings + coverage_warnings,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            "final_gap_verify": stats,
            "section_coverage": section_coverage_meta,
        },
    }
