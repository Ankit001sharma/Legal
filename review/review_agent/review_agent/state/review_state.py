"""LangGraph state for the section-first compliance review pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from document_core.schemas.chunk import IngestResult, IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ReviewReport


class ReviewState(TypedDict, total=False):
    tenant_id: str
    contract_text: str
    contract_title: str
    contract_document_id: str | None
    policy_texts: list[dict[str, Any]]
    contract_type: str | None
    policy_type: str | None

    ingest_result: IngestResult
    contract_sections: list[IndexedChunk]
    indexed_policies: list[dict[str, Any]]
    policy_document_ids: list[str]
    contract_routing: dict[str, Any]
    discovered_policies: list[dict[str, Any]]
    discovered_policy_document_ids: list[str]
    discovery_warnings: list[str]
    policy_refs: list[str]
    fetched_policy_refs: list[str]
    policy_ref_by_document_id: dict[str, str]

    section_retrieval_by_id: dict[str, dict[str, Any]]
    section_review_sections: list[dict[str, Any]]
    section_compare_items: list[dict[str, Any]]
    gap_section_ids: list[str]
    unclear_finding_ids: list[str]
    conflict_pairs: list[list[str]]
    final_verify_stats: dict[str, Any]
    section_coverage: dict[str, Any]
    compliance_stats: dict[str, Any]
    superseded_finding_ids: list[str]

    findings: list[ComplianceFinding]
    grounded_findings: list[ComplianceFinding]
    warnings: Annotated[list[str], operator.add]
    report: ReviewReport

    thread_id: str
    memory_context: str
    memory_hits: list[dict[str, Any]]
    memory_saved: bool
    memory_save_message: str
