"""Tests for post-grounding rationale guard (P6.1)."""

from __future__ import annotations

import uuid

import pytest
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.config import ReviewSettings
from review_agent.services.guard_pass import RationaleGuardResult, run_guard_pass


def _finding(**overrides) -> ComplianceFinding:
    base = ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:cap",
        dimension_label="Liability Cap",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="fees paid in three months",
        policy_quote="fees paid in twelve months",
        contract_section_id="s1",
        rationale="Contract cap is below the policy minimum.",
        grounded=True,
        metadata={"source": "playbook_compare"},
    )
    return base.model_copy(update=overrides)


@pytest.mark.asyncio
async def test_guard_downgrades_when_not_supported(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        return RationaleGuardResult(supported=False, reason="Rationale overstates gap.")

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_checked"] == 1
    assert stats["guard_failed"] == 1
    assert updated[0].status == ComplianceStatus.INCONCLUSIVE
    assert updated[0].metadata.get("guard_failed") is True
    assert warnings


@pytest.mark.asyncio
async def test_guard_keeps_supported_finding(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        return RationaleGuardResult(supported=True, reason="ok")

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    original = _finding()
    updated, _warnings, stats = await run_guard_pass(
        [original],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_failed"] == 0
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert updated[0].metadata.get("guard_failed") is None


@pytest.mark.asyncio
async def test_guard_skips_insufficient_policy_context():
    finding = _finding(status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT)
    updated, _warnings, stats = await run_guard_pass(
        [finding],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_skipped"] == 1
    assert stats["guard_checked"] == 0
    assert updated[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_guard_disabled_is_noop():
    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(guard_pass_enabled=False),
    )
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert stats["guard_skipped"] == 1
    assert not warnings
