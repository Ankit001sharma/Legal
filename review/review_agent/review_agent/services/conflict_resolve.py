"""Emit explicit POLICY_CONFLICT when re-compare leaves material disagreement."""

from __future__ import annotations

import uuid

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

_MATERIAL_STATUSES = frozenset(
    {ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT, ComplianceStatus.POLICY_CONFLICT}
)


def _material_statuses(findings: list[ComplianceFinding]) -> set[ComplianceStatus]:
    return {f.status for f in findings if f.status in _MATERIAL_STATUSES}


def emit_skipped_conflict_recompare(
    section_id: str,
    prior_findings: list[ComplianceFinding],
) -> ComplianceFinding:
    """Emit POLICY_CONFLICT when conflict re-compare could not run."""
    conflict_ids = [f.finding_id for f in prior_findings if f.contract_section_id == section_id]
    contract_quote = next(
        (f.contract_quote for f in prior_findings if f.contract_quote),
        "",
    )
    policy_quotes = [
        f.policy_quote.strip()
        for f in prior_findings
        if f.policy_quote and f.contract_section_id == section_id
    ]
    title = prior_findings[0].dimension_label if prior_findings else section_id
    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=f"{section_id}:policy_conflict",
        dimension_label=title or section_id,
        status=ComplianceStatus.POLICY_CONFLICT,
        severity=Severity.CRITICAL,
        contract_quote=contract_quote,
        policy_quote="\n---\n".join(policy_quotes)[:2000],
        contract_section_id=section_id,
        rationale="Conflict could not be re-evaluated: no policy hits available.",
        metadata={
            "source": "conflict_resolver",
            "conflict_unresolved": True,
            "conflict_recompare_skipped": True,
            "conflict_finding_ids": conflict_ids,
        },
    )


def emit_unresolved_policy_conflict(
    section_id: str,
    prior_findings: list[ComplianceFinding],
    new_findings: list[ComplianceFinding],
) -> ComplianceFinding | None:
    """Return one POLICY_CONFLICT row when material statuses still disagree."""
    combined = [
        f
        for f in (*prior_findings, *new_findings)
        if f.contract_section_id == section_id
    ]
    if len(_material_statuses(combined)) <= 1:
        return None

    conflict_ids = [f.finding_id for f in combined]
    policy_quotes: list[str] = []
    contract_quote = ""
    labels: list[str] = []
    for finding in combined:
        labels.append(f"{finding.status.value}: {finding.dimension_label}")
        if finding.contract_quote and not contract_quote:
            contract_quote = finding.contract_quote
        if finding.policy_quote:
            policy_quotes.append(finding.policy_quote.strip())

    policy_quote = "\n---\n".join(policy_quotes)[:2000]
    title = combined[0].dimension_label or section_id
    rationale = (
        "Policies or prior assessments still disagree after re-compare: "
        + "; ".join(labels[:6])
    )[:2000]

    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=f"{section_id}:policy_conflict",
        dimension_label=title,
        status=ComplianceStatus.POLICY_CONFLICT,
        severity=Severity.CRITICAL,
        contract_quote=contract_quote,
        policy_quote=policy_quote,
        contract_section_id=section_id,
        rationale=rationale,
        metadata={
            "source": "conflict_resolver",
            "conflict_unresolved": True,
            "conflict_finding_ids": conflict_ids,
        },
    )
