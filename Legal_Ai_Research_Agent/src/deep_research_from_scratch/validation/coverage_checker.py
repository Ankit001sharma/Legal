"""Phase 6 — Coverage completeness checker."""

from __future__ import annotations

import re

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.domains import get_domain_adapter
from deep_research_from_scratch.validation.models import CoverageDimension, CoverageReport

_COVERAGE_DIMENSIONS = [
    "core_concepts",
    "key_facts",
    "major_arguments",
    "alternative_viewpoints",
    "risks",
    "limitations",
    "recent_developments",
    "open_questions",
]

_DIMENSION_LABELS = {
    "core_concepts": "Core Concepts",
    "key_facts": "Key Facts",
    "major_arguments": "Major Arguments",
    "alternative_viewpoints": "Alternative Viewpoints",
    "risks": "Risks",
    "limitations": "Limitations",
    "recent_developments": "Recent Developments",
    "open_questions": "Open Questions",
}


def _dimension_keywords(name: str) -> set[str]:
    mapping = {
        "core_concepts": {"definition", "concept", "framework", "principle", "scope"},
        "key_facts": {"fact", "held", "rule", "section", "statute", "judgment"},
        "major_arguments": {"argument", "reasoning", "analysis", "issue", "question"},
        "alternative_viewpoints": {"contrary", "conflict", "dissent", "alternative", "counter"},
        "risks": {"risk", "penalty", "liability", "consequence", "exposure"},
        "limitations": {"limit", "exception", "qualification", "restrict", "caveat"},
        "recent_developments": {"2020", "2021", "2022", "2023", "2024", "2025", "recent", "amendment"},
        "open_questions": {"uncertain", "unsettled", "pending", "open", "debate"},
    }
    return mapping.get(name, set())


def _source_usable(source: RetrievedSource) -> bool:
    val = source.validation
    if val is None:
        return True
    if hasattr(val, "usable"):
        return bool(val.usable)
    return bool(val.get("usable", True))


def check_coverage(
    research_brief: str,
    sources: list[RetrievedSource],
    notes: list[str] | None = None,
) -> CoverageReport:
    """Evaluate topic coverage across standard dimensions."""
    adapter = get_domain_adapter(app_config.VALIDATION_DOMAIN)
    brief_kw = adapter.extract_keywords(research_brief)
    corpus_parts = [research_brief] + (notes or [])
    for src in sources:
        if _source_usable(src):
            corpus_parts.append(f"{src.title} {src.excerpt}")
    corpus = " ".join(corpus_parts).lower()
    corpus_kw = adapter.extract_keywords(corpus)

    dimensions: list[CoverageDimension] = []
    covered_count = 0

    for dim_name in _COVERAGE_DIMENSIONS:
        dim_kw = _dimension_keywords(dim_name)
        overlap = dim_kw & (corpus_kw | brief_kw)
        source_ids = [
            s.source_index
            for s in sources
            if _source_usable(s)
            and any(k in f"{s.title} {s.excerpt}".lower() for k in dim_kw)
        ]
        covered = len(overlap) >= 1 or len(source_ids) >= 1
        if dim_name == "recent_developments":
            covered = covered or bool(re.search(r"\b202[0-5]\b", corpus))
        if covered:
            covered_count += 1
        gap = ""
        if not covered:
            gap = f"Insufficient evidence for {_DIMENSION_LABELS[dim_name]}"
        dimensions.append(
            CoverageDimension(
                name=_DIMENSION_LABELS[dim_name],
                covered=covered,
                evidence_source_ids=source_ids,
                gap_description=gap,
            )
        )

    coverage_pct = (covered_count / len(_COVERAGE_DIMENSIONS)) * 100 if _COVERAGE_DIMENSIONS else 0.0
    missing = [d.gap_description for d in dimensions if not d.covered and d.gap_description]

    return CoverageReport(
        dimensions=dimensions,
        coverage_pct=round(coverage_pct, 1),
        missing_areas=missing,
    )


def build_gap_queries(coverage: CoverageReport, research_brief: str) -> list[str]:
    """Generate targeted search queries for missing coverage areas."""
    if coverage.coverage_pct >= app_config.MIN_COVERAGE_SCORE:
        return []
    queries: list[str] = []
    for dim in coverage.dimensions:
        if not dim.covered:
            queries.append(f"{research_brief} {dim.name}")
    return queries[:3]
