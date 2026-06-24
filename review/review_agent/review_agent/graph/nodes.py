"""LangGraph nodes for section-first contract compliance review."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    GroundingCheckRequest,
    IndexedChunk,
    IngestRequest,
    IngestResult,
    ListSectionsRequest,
    StructureConfidence,
)
from document_core.schemas.compliance import ComplianceStatus, ReviewReport
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.policy_catalog import get_policy_catalog, index_fetched_policy
from review_agent.config import get_settings
from review_agent.reports.generator import render_markdown_report
from review_agent.reports.summary_llm import maybe_llm_summary_paragraph
from review_agent.services.finding_enrich import (
    build_policy_title_map,
    enrich_findings_policy_titles,
)
from review_agent.services.guard_pass import run_guard_pass
from review_agent.services.review_artifact import build_review_artifact
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections
from review_agent.state.review_state import ReviewState


async def contract_parser_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    doc_id_raw = state.get("contract_document_id")
    if doc_id_raw:
        document_id = UUID(str(doc_id_raw))
        sections = await client.list_sections(
            ListSectionsRequest(
                tenant_id=state["tenant_id"],
                document_id=document_id,
                kind=DocumentKind.CONTRACT,
            )
        )
        if not sections:
            raise ValueError(f"contract document not indexed: {document_id}")

        title = (
            state.get("contract_title")
            or str(sections[0].metadata.get("document_title") or "").strip()
            or "Contract"
        )
        ingest_result = IngestResult(
            document_id=document_id,
            tenant_id=state["tenant_id"],
            kind=DocumentKind.CONTRACT,
            title=title,
            parent_count=len(sections),
            child_count=0,
            structure_confidence=StructureConfidence.HIGH,
            warnings=["loaded existing contract by document_id; skipped re-ingest"],
        )
        return {
            "ingest_result": ingest_result,
            "contract_sections": sections,
            "warnings": list(ingest_result.warnings),
        }

    contract_text = (state.get("contract_text") or "").strip()
    if not contract_text:
        raise ValueError("contract_text required when contract_document_id is not set")

    request = IngestRequest(
        tenant_id=state["tenant_id"],
        title=state.get("contract_title") or "Contract",
        kind=DocumentKind.CONTRACT,
        text=contract_text,
    )
    ingest_result = await client.ingest_document(request)
    warnings = list(ingest_result.warnings)
    if ingest_result.structure_confidence.value != "high":
        warnings.append(
            f"contract structure confidence is {ingest_result.structure_confidence.value}; "
            "section boundaries may affect review accuracy."
        )
    return {
        "ingest_result": ingest_result,
        "warnings": warnings,
    }


async def index_policies_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    warnings: list[str] = []
    indexed_policies: list[dict[str, Any]] = list(state.get("indexed_policies") or [])
    indexed_ids = {str(entry.get("document_id")) for entry in indexed_policies if entry.get("document_id")}
    fetched_refs: set[str] = set(state.get("fetched_policy_refs") or [])
    ref_by_doc: dict[str, str] = dict(state.get("policy_ref_by_document_id") or {})

    catalog = get_policy_catalog(
        catalog_url=settings.policy_catalog_url,
        fetch_enabled=settings.policy_fetch_enabled,
    )

    for ref in state.get("policy_refs") or []:
        if ref in fetched_refs:
            continue
        if catalog is None:
            warnings.append(f"policy_ref {ref!r} skipped: no catalog configured")
            continue
        document = await catalog.fetch_policy(state["tenant_id"], ref)
        if document is None:
            warnings.append(f"policy_ref {ref!r} not found in catalog")
            continue
        _result, entry = await index_fetched_policy(
            client,
            tenant_id=state["tenant_id"],
            document=document,
            policy_ref=ref,
            default_policy_type=state.get("policy_type"),
        )
        indexed_policies.append(entry)
        fetched_refs.add(ref)
        ref_by_doc[entry["document_id"]] = ref

    for entry in state.get("discovered_policies") or []:
        doc_id = str(entry.get("document_id") or "")
        if not doc_id or doc_id in indexed_ids:
            continue
        try:
            sections = await client.list_sections(
                ListSectionsRequest(
                    tenant_id=state["tenant_id"],
                    document_id=UUID(doc_id),
                    kind=DocumentKind.POLICY,
                )
            )
        except (ValueError, TypeError):
            warnings.append(f"discovered policy {doc_id!r} has invalid document_id")
            continue
        if not sections:
            warnings.append(f"discovered policy {doc_id!r} not found in document store")
            continue
        indexed_policies.append(
            {
                "document_id": doc_id,
                "title": entry.get("title") or sections[0].metadata.get("document_title") or sections[0].title or "Policy",
                "policy_type": entry.get("policy_type"),
                "applies_to_contract_types": list(entry.get("applies_to_contract_types") or []),
            }
        )
        indexed_ids.add(doc_id)

    for idx, policy in enumerate(state.get("policy_texts") or []):
        title = policy.get("title") or f"Policy {idx + 1}"
        text = policy.get("text", "").strip()
        if not text:
            warnings.append(f"skipped empty policy: {title}")
            continue

        applies = policy.get("applies_to_contract_types") or (
            [state["contract_type"]] if state.get("contract_type") else []
        )
        categories = list(policy.get("categories") or [])
        result = await client.index_policy(
            IngestRequest(
                tenant_id=state["tenant_id"],
                title=title,
                kind=DocumentKind.POLICY,
                text=text,
                policy_type=policy.get("policy_type") or state.get("policy_type"),
                applies_to_contract_types=applies,
                categories=categories,
            )
        )
        indexed_policies.append(
            {
                "document_id": str(result.document_id),
                "title": title,
                "policy_type": policy.get("policy_type") or state.get("policy_type"),
                "applies_to_contract_types": list(applies),
            }
        )
        if not categories:
            warnings.append(
                f"inline policy {title!r} has no categories; metadata retrieval may be weaker."
            )

    return {
        "warnings": warnings,
        "indexed_policies": indexed_policies,
        "fetched_policy_refs": sorted(fetched_refs),
        "policy_ref_by_document_id": ref_by_doc,
    }


async def clause_detection_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    existing = state.get("contract_sections")
    if existing:
        return {"contract_sections": existing}

    ingest = state["ingest_result"]
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=state["tenant_id"],
            document_id=ingest.document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    return {"contract_sections": sections}


async def grounding_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    ingest = state["ingest_result"]
    grounded: list = []
    warnings: list[str] = []
    title_map = build_policy_title_map(
        state.get("indexed_policies") or [],
        state.get("discovered_policies"),
    )
    findings = enrich_findings_policy_titles(state.get("findings") or [], title_map)

    for finding in findings:
        if finding.status.value == "INSUFFICIENT_POLICY_CONTEXT":
            grounded.append(finding.model_copy(update={"grounded": True}))
            continue

        if not finding.contract_quote and not finding.policy_quote:
            continue

        contract_ok = True
        policy_ok = True
        if finding.contract_quote:
            contract_check = await client.verify_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=ingest.document_id,
                    quote=finding.contract_quote,
                    section_id=finding.contract_section_id,
                )
            )
            contract_ok = contract_check.grounded

        if finding.policy_quote and finding.policy_document_id:
            policy_check = await client.verify_policy_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=finding.policy_document_id,
                    quote=finding.policy_quote,
                    section_id=finding.policy_section_id,
                )
            )
            policy_ok = policy_check.grounded

        ok = contract_ok and policy_ok
        if ok:
            grounded.append(finding.model_copy(update={"grounded": True}))
        elif settings.grounding_downgrade_not_drop:
            meta = dict(finding.metadata or {})
            meta["grounding_failed"] = True
            meta["prior_status"] = finding.status.value
            grounded.append(
                finding.model_copy(
                    update={
                        "status": ComplianceStatus.INCONCLUSIVE,
                        "grounded": False,
                        "metadata": meta,
                        "contract_quote": finding.contract_quote if contract_ok else "",
                        "policy_quote": finding.policy_quote if policy_ok else "",
                    }
                )
            )
            warnings.append(
                f"finding downgraded to INCONCLUSIVE (grounding failed): {finding.dimension_label}"
            )
        else:
            warnings.append(
                f"finding dropped (grounding failed): {finding.dimension_label}"
            )

    guard_stats: dict[str, int] = {}
    if settings.guard_pass_enabled:
        grounded, guard_warnings, guard_stats = await run_guard_pass(
            grounded,
            settings=settings,
        )
        warnings.extend(guard_warnings)

    section_coverage_meta = dict(state.get("section_coverage") or {})
    if settings.grounding_rerun_coverage and settings.enforce_section_coverage:
        raw_sections = state.get("section_review_sections") or state.get("contract_sections") or []
        reviewable = reviewable_sections(
            [IndexedChunk.model_validate(s) for s in raw_sections],
            min_chars=settings.review_min_section_chars,
        )
        coverage = ensure_section_coverage(
            reviewable,
            grounded,
            min_chars=settings.review_min_section_chars,
        )
        grounded = coverage.findings
        section_coverage_meta = {
            **section_coverage_meta,
            "post_grounding_reviewable_count": coverage.reviewable_count,
            "post_grounding_uncovered_before": coverage.uncovered_before,
            "post_grounding_backfill_count": coverage.backfill_count,
        }
        warnings.extend(coverage.warnings)

    return {
        "grounded_findings": grounded,
        "warnings": warnings,
        "section_coverage": section_coverage_meta,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            **guard_stats,
        },
    }


async def report_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    ingest = state["ingest_result"]
    findings = state.get("grounded_findings") or []
    stats = dict(state.get("compliance_stats") or {})
    stats["policy_conflict_count"] = sum(
        1 for f in findings if f.status == ComplianceStatus.POLICY_CONFLICT
    )
    coverage_meta = dict(state.get("section_coverage") or {})
    finding_section_ids = sorted(
        {f.contract_section_id for f in findings if f.contract_section_id}
    )
    artifact = build_review_artifact(state, findings=findings, settings=settings)
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
            "review_policy_source": "tenant_auto",
            "contract_document_id": str(ingest.document_id),
            "fetched_policy_refs": list(state.get("fetched_policy_refs") or []),
            "compliance_stats": stats,
            "section_retrieval_count": len(state.get("section_retrieval_by_id") or {}),
            "section_compare_count": len(state.get("section_compare_items") or []),
            "gap_section_count": len(state.get("gap_section_ids") or []),
            "unclear_finding_count": len(state.get("unclear_finding_ids") or []),
            "conflict_pair_count": len(state.get("conflict_pairs") or []),
            "final_verify_stats": dict(state.get("final_verify_stats") or {}),
            "section_coverage": coverage_meta,
            "reviewable_section_count": coverage_meta.get("reviewable_count", 0),
            "finding_section_ids": finding_section_ids,
            "discovered_policy_document_ids": list(
                state.get("discovered_policy_document_ids") or []
            ),
            "routing_topics": list((state.get("contract_routing") or {}).get("topics") or []),
            "discovery_warnings": list(state.get("discovery_warnings") or []),
            "pipeline": "section_first",
            "artifact": artifact.model_dump(mode="json"),
        },
    )
    llm_paragraph, llm_warning = await maybe_llm_summary_paragraph(
        report,
        artifact=artifact,
        settings=settings,
    )
    if llm_warning:
        report.warnings.append(llm_warning)
    backfill_count = int(coverage_meta.get("backfill_count") or 0)
    if backfill_count > 0:
        report.warnings.append(f"{backfill_count} section(s) required coverage backfill")
    if state.get("memory_context"):
        report.metadata["memory_context_preview"] = state["memory_context"][:500]
    report.summary_markdown = render_markdown_report(
        report,
        artifact=artifact,
        llm_summary_paragraph=llm_paragraph,
    )
    return {"report": report}
