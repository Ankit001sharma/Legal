"""Deterministic compliance comparison (LLM hook added later)."""

from __future__ import annotations

import uuid

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from document_core.search.lexical import score_query


def compare_sections(
    *,
    dimension_id: str,
    dimension_label: str,
    contract_hits: list[RetrievalHit],
    policy_hits: list[RetrievalHit],
) -> ComplianceFinding | None:
    """Compare retrieved contract and policy parent sections."""
    if not policy_hits:
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            dimension_id=dimension_id,
            dimension_label=dimension_label,
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            rationale="No matching policy section retrieved for this dimension.",
        )

    if not contract_hits:
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            dimension_id=dimension_id,
            dimension_label=dimension_label,
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            policy_quote=_short_quote(policy_hits[0].parent_chunk.text),
            policy_section_id=policy_hits[0].parent_chunk.section_id,
            policy_document_id=policy_hits[0].parent_chunk.document_id,
            rationale="Policy requirement found but no matching contract clause retrieved.",
        )

    contract = contract_hits[0].parent_chunk
    policy = policy_hits[0].parent_chunk

    overlap = score_query(policy.text, contract.text)
    policy_specific = score_query(policy.text, policy.text)

    if overlap < 0.05:
        status = ComplianceStatus.NON_COMPLIANT
        severity = Severity.IMPORTANT
        rationale = (
            "Contract section weakly aligns with policy language for this dimension "
            f"(lexical overlap={overlap:.2f})."
        )
    elif overlap >= policy_specific * 0.35:
        status = ComplianceStatus.COMPLIANT
        severity = Severity.INFO
        rationale = (
            "Contract section appears to cover policy requirements for this dimension "
            f"(lexical overlap={overlap:.2f})."
        )
    else:
        status = ComplianceStatus.INCONCLUSIVE
        severity = Severity.INFO
        rationale = (
            "Partial overlap between contract and policy; manual or LLM review recommended "
            f"(overlap={overlap:.2f})."
        )

    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        status=status,
        severity=severity,
        contract_quote=_short_quote(contract.text),
        policy_quote=_short_quote(policy.text),
        contract_section_id=contract.section_id,
        policy_section_id=policy.section_id,
        policy_document_id=policy.document_id,
        rationale=rationale,
    )


def _short_quote(text: str, max_len: int = 320) -> str:
    """Extract a verifiable quote (no ellipsis — grounding requires substring match)."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut
