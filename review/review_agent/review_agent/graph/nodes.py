"""LangGraph nodes for contract compliance review."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    GroundingCheckRequest,
    IngestRequest,
    ListSectionsRequest,
    SearchRequest,
)
from document_core.schemas.compliance import ReviewReport
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.dimensions.loader import load_dimensions
from review_agent.reports.generator import render_markdown_report
from review_agent.services.compliance import compare_sections
from review_agent.state.review_state import ReviewState


async def contract_parser_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    request = IngestRequest(
        tenant_id=state["tenant_id"],
        title=state.get("contract_title") or "Contract",
        kind=DocumentKind.CONTRACT,
        text=state["contract_text"],
    )
    ingest_result = await client.ingest_document(request)
    warnings = list(ingest_result.warnings)
    return {
        "ingest_result": ingest_result,
        "warnings": warnings,
    }


async def index_policies_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    warnings: list[str] = []
    for idx, policy in enumerate(state.get("policy_texts") or []):
        title = policy.get("title") or f"Policy {idx + 1}"
        text = policy.get("text", "").strip()
        if not text:
            warnings.append(f"skipped empty policy: {title}")
            continue
        await client.index_policy(
            IngestRequest(
                tenant_id=state["tenant_id"],
                title=title,
                kind=DocumentKind.POLICY,
                text=text,
                policy_type=policy.get("policy_type") or state.get("policy_type"),
                applies_to_contract_types=policy.get("applies_to_contract_types")
                or ([state["contract_type"]] if state.get("contract_type") else []),
            )
        )
    return {"warnings": warnings}


async def clause_detection_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    ingest = state["ingest_result"]
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=state["tenant_id"],
            document_id=ingest.document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    return {"contract_sections": sections}


async def policy_retrieval_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    ingest = state["ingest_result"]
    dimensions = load_dimensions()
    policy_hits: dict[str, list] = {}
    contract_hits: dict[str, list] = {}

    for dimension_id, spec in dimensions.items():
        label = spec.get("label", dimension_id)
        queries = spec.get("search_queries") or [label]
        query = queries[0]

        policy_hits[dimension_id] = await client.search_policy(
            SearchRequest(
                tenant_id=state["tenant_id"],
                query=query,
                kind=DocumentKind.POLICY,
                policy_type=state.get("policy_type"),
                contract_type=state.get("contract_type"),
                top_k=3,
            )
        )
        contract_hits[dimension_id] = await client.search_contract(
            SearchRequest(
                tenant_id=state["tenant_id"],
                query=query,
                document_id=ingest.document_id,
                kind=DocumentKind.CONTRACT,
                top_k=3,
            )
        )

    return {
        "policy_hits_by_dimension": policy_hits,
        "contract_hits_by_dimension": contract_hits,
    }


async def compliance_review_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    dimensions = load_dimensions()
    findings = []
    for dimension_id, spec in dimensions.items():
        finding = compare_sections(
            dimension_id=dimension_id,
            dimension_label=spec.get("label", dimension_id),
            contract_hits=state.get("contract_hits_by_dimension", {}).get(dimension_id, []),
            policy_hits=state.get("policy_hits_by_dimension", {}).get(dimension_id, []),
        )
        if finding:
            findings.append(finding)
    return {"findings": findings}


async def grounding_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    ingest = state["ingest_result"]
    grounded: list = []

    for finding in state.get("findings") or []:
        if finding.status.value == "INSUFFICIENT_POLICY_CONTEXT":
            grounded.append(finding.model_copy(update={"grounded": True}))
            continue

        if not finding.contract_quote and not finding.policy_quote:
            continue

        ok = True
        if finding.contract_quote:
            contract_check = await client.verify_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=ingest.document_id,
                    quote=finding.contract_quote,
                    section_id=finding.contract_section_id,
                )
            )
            ok = ok and contract_check.grounded

        if finding.policy_quote and finding.policy_document_id:
            policy_check = await client.verify_policy_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=finding.policy_document_id,
                    quote=finding.policy_quote,
                    section_id=finding.policy_section_id,
                )
            )
            ok = ok and policy_check.grounded

        if ok:
            grounded.append(finding.model_copy(update={"grounded": True}))

    return {"grounded_findings": grounded}


async def report_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    ingest = state["ingest_result"]
    findings = state.get("grounded_findings") or []
    report = ReviewReport(
        tenant_id=state["tenant_id"],
        contract_document_id=ingest.document_id,
        contract_title=state.get("contract_title") or ingest.title,
        findings=findings,
        warnings=list(state.get("warnings") or []),
        structure_confidence=ingest.structure_confidence.value,
        metadata={
            "thread_id": state.get("thread_id"),
            "memory_hits": len(state.get("memory_hits") or []),
        },
    )
    if state.get("memory_context"):
        report.metadata["memory_context_preview"] = state["memory_context"][:500]
    report.summary_markdown = render_markdown_report(report)
    return {"report": report}
