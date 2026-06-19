"""Tests for section-first finding merge."""

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_merge import merge_section_findings, section_items_to_findings
from review_agent.services.playbook_context import PlaybookHints


def test_merge_dedupes_compare_items():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Cap missing from contract section.",
        ),
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Duplicate should be dropped.",
        ),
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
    }
    merged = merge_section_findings(items, bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].status == ComplianceStatus.NON_COMPLIANT
    assert not merged.warnings


def test_merge_adds_no_policy_gap():
    bundles = {
        "s2": SectionRetrievalBundle(section_id="s2", categories=["privacy"], policy_hits=[]),
    }
    merged = merge_section_findings([], bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert merged.findings[0].metadata.get("gap_type") == "no_policy"
    assert merged.gap_section_ids == ["s2"]
    assert merged.warnings


def test_merge_adds_compare_omitted_gap():
    from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=__import__("uuid").uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify.",
        ),
        score=0.9,
    )
    bundles = {
        "s3": SectionRetrievalBundle(
            section_id="s3",
            categories=["indemnity"],
            policy_hits=[policy_hit],
        ),
    }
    merged = merge_section_findings([], bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].metadata.get("gap_type") == "compare_omitted"
    assert merged.gap_section_ids == ["s3"]


def test_section_items_playbook_metadata():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Cap missing.",
            confidence=0.9,
        )
    ]
    hints = {
        "550e8400-e29b-41d4-a716-446655440000": PlaybookHints(
            policy_ref="vendor-liability",
            review_guidance="Require 12 month cap.",
        )
    }
    findings = section_items_to_findings(items, hints_by_document=hints)
    assert findings[0].metadata.get("source") == "playbook_compare"
    assert findings[0].metadata.get("policy_ref") == "vendor-liability"
    assert findings[0].metadata.get("playbook_guidance_used") is True
