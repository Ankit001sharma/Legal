"""LangGraph state for the compliance review pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any
from uuid import UUID

from typing_extensions import TypedDict

from document_core.schemas.chunk import IngestResult, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ReviewReport


class ReviewState(TypedDict, total=False):
    tenant_id: str
    contract_text: str
    contract_title: str
    policy_texts: list[dict[str, Any]]
    contract_type: str | None
    policy_type: str | None

    ingest_result: IngestResult
    contract_sections: list[IndexedChunk]
    policy_hits_by_dimension: dict[str, list[RetrievalHit]]
    contract_hits_by_dimension: dict[str, list[RetrievalHit]]
    findings: Annotated[list[ComplianceFinding], operator.add]
    grounded_findings: list[ComplianceFinding]
    warnings: Annotated[list[str], operator.add]
    report: ReviewReport

    thread_id: str
    memory_context: str
    memory_hits: list[dict[str, Any]]
    memory_saved: bool
    memory_save_message: str
