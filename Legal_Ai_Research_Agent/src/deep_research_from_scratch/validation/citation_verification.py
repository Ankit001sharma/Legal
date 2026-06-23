"""Phase 5 — Citation verification."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    extract_citations,
    extract_inline_citation_numbers,
    extract_sources_section_urls,
    normalize_url,
)
from deep_research_from_scratch.validation.models import CitationCoverageReport, ValidatedClaim


def verify_citations(
    report: str,
    findings: str,
    sources: list[RetrievedSource],
    claims: list[ValidatedClaim] | None = None,
) -> CitationCoverageReport:
    """Verify citation coverage and mapping."""
    claims = claims or []
    findings_norm = re.sub(r"\s+", " ", findings or "").strip().upper()
    report_citations = extract_citations(report)
    fabricated = [c for c in report_citations if c not in findings_norm]

    registry_urls = {normalize_url(s.url) for s in sources if s.url}
    sources_mapping = extract_sources_section_urls(report)
    inline_numbers = extract_inline_citation_numbers(report)
    unmapped = [
        n
        for n in inline_numbers
        if n not in sources_mapping
        or normalize_url(sources_mapping[n]) not in registry_urls
    ]

    total = len(claims) if claims else max(len(report_citations), 1)
    unsupported = sum(1 for c in claims if c.support_level == "unsupported")
    supported = total - unsupported
    if claims:
        supported = sum(
            1 for c in claims if c.support_level in ("direct", "indirect", "weak")
        )
        unsupported = total - supported

    # Penalize fabricated citations
    if fabricated:
        unsupported += len(fabricated)
    if unmapped:
        unsupported += len(unmapped)

    coverage = (supported / total * 100) if total else 100.0
    coverage = max(0.0, min(100.0, coverage - len(fabricated) * 5))

    return CitationCoverageReport(
        total_claims=total,
        supported_claims=supported,
        unsupported_claims=unsupported,
        coverage_pct=round(coverage, 1),
    )
