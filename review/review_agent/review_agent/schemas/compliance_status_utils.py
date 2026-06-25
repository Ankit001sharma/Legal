"""Shared ComplianceStatus normalization for LLM structured output (P1-5)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus

# Extend when new model typos appear in production logs.
_STATUS_TYPO_MAP: dict[str, ComplianceStatus] = {
    "INSUFFICIENT_POLIC_CONTEXT": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    "INSUFFICIENT_POLICY_CONTEX": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    "INSUFFICIENT_POLICY_CONTXT": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    "INSUFFICENT_POLICY_CONTEXT": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    "NONCOMPLIANT": ComplianceStatus.NON_COMPLIANT,
    "NON-COMPLIANT": ComplianceStatus.NON_COMPLIANT,
    "INCONCLUSIVE_STATUS": ComplianceStatus.INCONCLUSIVE,
    "POLICY_CONFLICTS": ComplianceStatus.POLICY_CONFLICT,
}


def normalize_compliance_status(value: object) -> object:
    """Map LLM typos / near-miss strings to ComplianceStatus before enum coercion."""
    if isinstance(value, ComplianceStatus):
        return value
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    mapped = _STATUS_TYPO_MAP.get(stripped.upper())
    if mapped is not None:
        return mapped
    try:
        return ComplianceStatus(stripped.upper())
    except ValueError:
        return stripped
