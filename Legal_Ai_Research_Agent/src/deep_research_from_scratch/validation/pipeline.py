"""Validation pipeline orchestrator for graph nodes."""

from __future__ import annotations

import re
from typing import Any

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    assign_source_indices,
    build_verification_corpus,
    filter_citable_sources,
)
from deep_research_from_scratch.validation.claim_validation import validate_claims
from deep_research_from_scratch.validation.citation_verification import verify_citations
from deep_research_from_scratch.validation.confidence_scorer import compute_confidence
from deep_research_from_scratch.validation.consensus_engine import apply_consensus, consensus_score
from deep_research_from_scratch.validation.coverage_checker import build_gap_queries, check_coverage
from deep_research_from_scratch.validation.evidence_extraction import extract_evidence, format_evidence_pack
from deep_research_from_scratch.validation.gap_detection import detect_research_gaps
from deep_research_from_scratch.validation.hallucination_detector import detect_hallucinations
from deep_research_from_scratch.validation.metrics import record_metrics_snapshot
from deep_research_from_scratch.validation.models import (
    LEGACY_REPORT_SECTIONS,
    REQUIRED_REPORT_SECTIONS,
    ResearchQualityMetrics,
    SourceValidation,
)
from deep_research_from_scratch.validation.source_validation import validate_sources
from deep_research_from_scratch.validation.statement_classification import classify_all_claims


def _collect_sources(state: dict[str, Any]) -> list[RetrievedSource]:
    sources: list[RetrievedSource] = []
    for item in state.get("retrieved_sources") or []:
        sources.append(
            item if isinstance(item, RetrievedSource) else RetrievedSource(**item)
        )
    return sources


def run_pre_write_validation(state: dict[str, Any]) -> dict:
    """Phases 1, 4, 6 — validate sources, filter, check coverage."""
    brief = state.get("research_brief") or ""
    sources = assign_source_indices(_collect_sources(state))
    notes = state.get("notes") or []

    validated_pairs = validate_sources(sources, brief)
    updated_sources = [src for src, _ in validated_pairs]
    validations = [val for _, val in validated_pairs]

    citable = filter_citable_sources(updated_sources)
    coverage = check_coverage(brief, citable, notes)
    gap_queries = build_gap_queries(coverage, brief)

    return {
        "retrieved_sources": updated_sources,
        "source_validations": validations,
        "coverage_gap_queries": gap_queries,
        "research_metrics": ResearchQualityMetrics(coverage_report=coverage),
    }


def extract_evidence_node(state: dict[str, Any]) -> dict:
    """Build evidence pack from validated citable sources."""
    sources = filter_citable_sources(_collect_sources(state))
    snippets = extract_evidence(sources)
    return {"evidence_pack": snippets}


def format_evidence_for_state(state: dict[str, Any]) -> str:
    """Format evidence pack text for writer prompt."""
    snippets = state.get("evidence_pack") or []
    return format_evidence_pack(snippets)


def check_required_sections(report: str) -> list[str]:
    """Return missing required sections (supports new + legacy schemas)."""
    report_low = (report or "").lower()
    missing_new = [s for s in REQUIRED_REPORT_SECTIONS if s.lower() not in report_low]
    if not missing_new:
        return []
    legacy_hits = sum(1 for s in LEGACY_REPORT_SECTIONS if s.lower() in report_low)
    if legacy_hits >= 5 and "disclaimer" in report_low:
        return []
    return missing_new


def run_post_write_validation(
    state: dict[str, Any],
    det: dict | None = None,
):
    """Phases 2-10 — full post-generation validation pipeline."""
    from deep_research_from_scratch.state_scope import VerificationResult
    report = state.get("final_report") or ""
    notes = state.get("notes") or []
    raw_notes = state.get("raw_notes") or []
    brief = state.get("research_brief") or ""
    sources = filter_citable_sources(_collect_sources(state))
    validations = state.get("source_validations") or []

    findings = build_verification_corpus(notes, raw_notes, sources)

    claims = validate_claims(report, notes, raw_notes, sources)
    primary_ids = {
        s.source_index
        for s in sources
        if s.authority_tier == "primary" and s.source_index
    }
    claims = classify_all_claims(claims, primary_ids)
    claims = apply_consensus(claims)

    citation_report = verify_citations(report, findings, sources, claims)
    coverage = check_coverage(brief, sources, notes)
    hallucination = detect_hallucinations(report, claims)
    cons_score = consensus_score(claims)

    metrics = compute_confidence(
        validations, citation_report, coverage, claims, cons_score
    )
    metrics.hallucination_report = hallucination
    metrics.research_gaps = detect_research_gaps(
        brief, coverage, claims, sources, findings, report
    )

    unsupported = [c.claim for c in claims if c.support_level == "unsupported"]
    missing_sections = check_required_sections(report)
    if det:
        missing_sections = det.get("missing_sections", missing_sections)

    passed = (
        citation_report.coverage_pct >= app_config.MIN_CITATION_COVERAGE_PCT
        and len(unsupported) == 0
        and not missing_sections
        and (det["passed"] if det else True)
    )

    required_fixes_parts = []
    if unsupported:
        required_fixes_parts.append(
            "Remove or mark as UNCERTAIN these unsupported claims: "
            + "; ".join(unsupported[:5])
        )
    if missing_sections:
        required_fixes_parts.append(
            "Add missing sections: " + ", ".join(missing_sections)
        )
    if metrics.research_gaps:
        required_fixes_parts.append(
            "Address research gaps: " + "; ".join(metrics.research_gaps[:3])
        )

    confidence_label = (
        "high"
        if metrics.overall_confidence_pct >= 75
        else "medium"
        if metrics.overall_confidence_pct >= 50
        else "low"
    )

    verification = VerificationResult(
        passed=passed,
        confidence=confidence_label,
        unsupported_claims=unsupported,
        missing_sections=missing_sections,
        required_fixes="\n".join(f"- {p}" for p in required_fixes_parts),
        overall_assessment=(
            f"Overall confidence {metrics.overall_confidence_pct}%. "
            f"Citation coverage {citation_report.coverage_pct}%."
        ),
        metrics=metrics,
    )

    return claims, metrics, verification


def sanitize_report(
    report: str,
    claims: list,
    metrics: ResearchQualityMetrics,
    verification: Any = None,
) -> str:
    """Strip unsupported claims and append structured quality sections."""
    sanitized = report or ""

    for claim in claims:
        if claim.support_level == "unsupported" and claim.claim in sanitized:
            replacement = f"[UNCERTAIN: insufficient evidence]"
            sanitized = sanitized.replace(claim.claim, replacement)

    # Remove duplicate UNCERTAIN blocks
    sanitized = re.sub(
        r"(\[UNCERTAIN: insufficient evidence\]\s*){2,}",
        "[UNCERTAIN: insufficient evidence] ",
        sanitized,
    )

    # Append structured sections if missing
    if "confidence assessment" not in sanitized.lower():
        conf_lines = [
            "",
            "## Confidence Assessment",
            "",
            f"**Overall Confidence:** {metrics.overall_confidence_pct}%",
            "",
        ]
        for reason in metrics.confidence_reasoning:
            conf_lines.append(f"- {reason}")
        sanitized += "\n".join(conf_lines)

    if "research gaps" not in sanitized.lower() and metrics.research_gaps:
        gap_lines = ["", "## Research Gaps", ""]
        for i, gap in enumerate(metrics.research_gaps, 1):
            gap_lines.append(f"{i}. {gap}")
        sanitized += "\n".join(gap_lines)

    if verification and not verification.passed:
        # Replace old Verification Caveats with metrics summary
        if "verification caveats" not in sanitized.lower():
            sanitized += (
                "\n\n## Verification Summary\n\n"
                f"> Citation coverage: {metrics.citation_coverage_pct}% | "
                f"Unsupported claims: {metrics.unsupported_claim_pct}% | "
                f"Hallucination rate: {metrics.hallucination_rate_pct}%\n"
            )

    return sanitized


def route_after_coverage(state: dict[str, Any]) -> str:
    """Decide whether to run gap research or proceed to evidence extraction."""
    metrics = state.get("research_metrics")
    retries = state.get("gap_research_retries", 0)
    gap_queries = state.get("coverage_gap_queries") or []

    if (
        gap_queries
        and retries < app_config.MAX_GAP_RESEARCH_ROUNDS
        and metrics
        and metrics.coverage_report.coverage_pct < app_config.MIN_COVERAGE_SCORE
    ):
        return "targeted_gap_research"
    return "extract_evidence"
