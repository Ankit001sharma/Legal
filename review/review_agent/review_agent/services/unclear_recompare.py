"""Rules for final-verify unclear re-compare eligibility (Phase 21 P0-B)."""

from __future__ import annotations

from typing import Literal

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus

UnclearReason = Literal[
    "low_confidence",
    "compare_failed",
    "rate_limited",
    "contract_silent",
    "gap_context",
    "inconclusive_other",
]

_LOW_CONFIDENCE_MAX = 0.5

_SILENT_MARKERS = (
    "does not mention",
    "does not reference",
    "not explicitly",
    "no explicit",
    "contract silent",
    "too general",
    "does not address",
    "no direct reference",
)


def classify_unclear_finding(finding: ComplianceFinding) -> UnclearReason:
    meta = finding.metadata or {}
    gap_type = str(meta.get("gap_type") or "")
    rationale = (finding.rationale or "").lower()
    source = str(meta.get("source") or "")

    if gap_type in ("no_policy", "compare_omitted"):
        return "gap_context"

    if rationale.startswith("section compare failed:"):
        if "429" in rationale or "rate limit" in rationale or "rate_limited" in rationale:
            return "rate_limited"
        return "compare_failed"

    if source == "section_compare_failed":
        return "compare_failed"

    if finding.status == ComplianceStatus.INCONCLUSIVE and any(m in rationale for m in _SILENT_MARKERS):
        return "contract_silent"

    confidence = meta.get("confidence")
    if (
        source == "playbook_compare"
        and confidence is not None
        and float(confidence) < _LOW_CONFIDENCE_MAX
        and finding.contract_section_id
        and (finding.policy_quote or meta.get("policy_document_id"))
    ):
        return "low_confidence"

    return "inconclusive_other"


def eligible_for_unclear_recompare(finding: ComplianceFinding) -> bool:
    return classify_unclear_finding(finding) == "low_confidence"


def section_has_grounded_non_compliant(
    section_id: str,
    findings: list[ComplianceFinding],
) -> bool:
    """Do not re-compare sections that already have a grounded violation."""
    for finding in findings:
        if finding.contract_section_id != section_id:
            continue
        if finding.status != ComplianceStatus.NON_COMPLIANT:
            continue
        if (finding.metadata or {}).get("source") != "playbook_compare":
            continue
        if finding.grounded is True:
            return True
        if (finding.contract_quote or "").strip() and (finding.policy_quote or "").strip():
            return True
    return False
