"""Tests for explicit POLICY_CONFLICT emission (P4.3)."""

import uuid

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.services.conflict_resolve import (
    emit_skipped_conflict_recompare,
    emit_unresolved_policy_conflict,
)


def _finding(
    *,
    section_id: str,
    status: ComplianceStatus,
    finding_id: str | None = None,
) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=finding_id or str(uuid.uuid4()),
        dimension_id=f"{section_id}:test",
        dimension_label="Liability",
        status=status,
        severity=Severity.CRITICAL,
        contract_quote="contract text here",
        policy_quote="policy text here",
        contract_section_id=section_id,
        rationale="test rationale",
    )


def test_emit_unresolved_policy_conflict_mixed_statuses():
    sid = "10.2"
    prior = [_finding(section_id=sid, status=ComplianceStatus.COMPLIANT, finding_id="a")]
    new = [_finding(section_id=sid, status=ComplianceStatus.NON_COMPLIANT, finding_id="b")]
    row = emit_unresolved_policy_conflict(sid, prior, new)
    assert row is not None
    assert row.status == ComplianceStatus.POLICY_CONFLICT
    assert row.metadata.get("source") == "conflict_resolver"
    assert set(row.metadata.get("conflict_finding_ids") or []) == {"a", "b"}


def test_emit_unresolved_policy_conflict_resolved():
    sid = "10.2"
    prior = [_finding(section_id=sid, status=ComplianceStatus.INCONCLUSIVE)]
    new = [_finding(section_id=sid, status=ComplianceStatus.NON_COMPLIANT)]
    assert emit_unresolved_policy_conflict(sid, prior, new) is None


def test_emit_skipped_conflict_recompare():
    sid = "3.1"
    prior = [_finding(section_id=sid, status=ComplianceStatus.COMPLIANT, finding_id="x")]
    row = emit_skipped_conflict_recompare(sid, prior)
    assert row.status == ComplianceStatus.POLICY_CONFLICT
    assert row.metadata.get("conflict_recompare_skipped") is True
    assert row.metadata.get("conflict_finding_ids") == ["x"]
