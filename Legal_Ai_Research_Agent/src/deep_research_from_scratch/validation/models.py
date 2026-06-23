"""Shared Pydantic schemas for the research validation pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SupportLevel = Literal["direct", "indirect", "weak", "unsupported"]
StatementClass = Literal[
    "FACT",
    "STRONG_INFERENCE",
    "INFERENCE",
    "HYPOTHESIS",
    "UNCERTAIN",
]
ConsensusLevel = Literal["high", "medium", "low", "conflicting"]


class SourceValidation(BaseModel):
    """Per-source quality scores (Phases 1 + 4)."""

    source: str
    authority_score: int = Field(ge=0, le=100)
    relevance_score: int = Field(ge=0, le=100)
    freshness_score: int = Field(ge=0, le=100)
    trust_score: int = Field(ge=0, le=100)
    usable: bool = True
    reason: str = ""


class EvidenceSnippet(BaseModel):
    """Atomic evidence extracted from a validated source."""

    snippet_id: str
    source_index: int
    text: str
    url: str = ""


class ValidatedClaim(BaseModel):
    """A claim with evidence traceability (Phases 2 + 3 + 8)."""

    claim: str
    support_level: SupportLevel = "unsupported"
    source_count: int = 0
    confidence: int = Field(default=0, ge=0, le=100)
    classification: StatementClass = "UNCERTAIN"
    supporting_source_ids: list[int] = Field(default_factory=list)
    contradicting_source_ids: list[int] = Field(default_factory=list)
    consensus: ConsensusLevel = "low"


class CitationCoverageReport(BaseModel):
    """Citation verification summary (Phase 5)."""

    total_claims: int = 0
    supported_claims: int = 0
    unsupported_claims: int = 0
    coverage_pct: float = 0.0


class CoverageDimension(BaseModel):
    """One dimension of topic coverage (Phase 6)."""

    name: str
    covered: bool = False
    evidence_source_ids: list[int] = Field(default_factory=list)
    gap_description: str = ""


class CoverageReport(BaseModel):
    """Topic coverage completeness (Phase 6)."""

    dimensions: list[CoverageDimension] = Field(default_factory=list)
    coverage_pct: float = 0.0
    missing_areas: list[str] = Field(default_factory=list)


class HallucinationReport(BaseModel):
    """Post-generation hallucination check (Phase 7)."""

    verified_claims: int = 0
    weak_claims: int = 0
    unsupported_claims: int = 0
    potential_hallucinations: int = 0
    flagged_claims: list[str] = Field(default_factory=list)


class ResearchQualityMetrics(BaseModel):
    """Aggregate quality metrics for a research run."""

    citation_coverage_pct: float = 0.0
    unsupported_claim_pct: float = 0.0
    hallucination_rate_pct: float = 0.0
    source_quality_score: float = 0.0
    relevance_score: float = 0.0
    coverage_completeness_pct: float = 0.0
    consensus_score: float = 0.0
    overall_confidence_pct: float = 0.0
    confidence_reasoning: list[str] = Field(default_factory=list)
    citation_report: CitationCoverageReport = Field(default_factory=CitationCoverageReport)
    coverage_report: CoverageReport = Field(default_factory=CoverageReport)
    hallucination_report: HallucinationReport = Field(default_factory=HallucinationReport)
    research_gaps: list[str] = Field(default_factory=list)


# Required report sections (10-section production structure)
REQUIRED_REPORT_SECTIONS = [
    "Executive Summary",
    "Direct Answer",
    "Key Findings",
    "Supporting Evidence",
    "Source Analysis",
    "Counterpoints and Alternative Views",
    "Risks and Limitations",
    "Research Gaps",
    "Confidence Assessment",
    "References and Citations",
]

# Legacy sections kept for backward-compatible checks
LEGACY_REPORT_SECTIONS = [
    "Questions Presented",
    "Brief Answer",
    "Statement of Facts",
    "Discussion",
    "Practical Guidance",
    "Conclusion",
    "Table of Authorities",
    "Disclaimer",
]
