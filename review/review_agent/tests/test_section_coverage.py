"""Tests for section coverage backfill."""

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections


def _section(section_id: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=__import__("uuid").uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


def test_reviewable_sections_respects_min_chars():
    sections = [
        _section("short", "tiny"),
        _section("long", "x" * 50),
    ]
    reviewable = reviewable_sections(sections, min_chars=40)
    assert [s.section_id for s in reviewable] == ["long"]


def test_ensure_section_coverage_backfills_missing():
    reviewable = [_section("s1", "a" * 50), _section("s2", "b" * 50)]
    existing = [
        ComplianceFinding(
            finding_id="f1",
            dimension_id="s1:x",
            dimension_label="s1",
            status=ComplianceStatus.COMPLIANT,
            contract_section_id="s1",
            rationale="Reviewed section one completely.",
        )
    ]
    result = ensure_section_coverage(reviewable, existing, min_chars=40)
    assert result.backfill_count == 1
    assert result.uncovered_before == ["s2"]
    assert len(result.findings) == 2
    backfill = result.findings[1]
    assert backfill.contract_section_id == "s2"
    assert backfill.metadata.get("gap_type") == "coverage_backfill"


def test_ensure_section_coverage_noop_when_complete():
    reviewable = [_section("s1", "a" * 50)]
    existing = [
        ComplianceFinding(
            finding_id="f1",
            dimension_id="s1:x",
            dimension_label="s1",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            contract_section_id="s1",
            rationale="Explicit gap for this section already exists.",
        )
    ]
    result = ensure_section_coverage(reviewable, existing, min_chars=40)
    assert result.backfill_count == 0
    assert len(result.findings) == 1
