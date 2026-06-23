#!/usr/bin/env python3
"""CI-runnable evaluation harness for research validation metrics.

Scores deterministic properties on synthetic reports without requiring LLM calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.pipeline import run_post_write_validation


def _load_golden(path: Path) -> list[dict]:
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def _synthetic_state(query: str) -> dict:
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/sample/",
            title="Sample Case",
            source_index=1,
            authority_tier="primary",
            fetched=True,
            excerpt=f"Findings related to {query} with established legal principles.",
        )
    ]
    report = (
        f"## Executive Summary\nSummary for {query}.\n\n"
        "## Direct Answer\n[ESTABLISHED] The law supports the query topic.\n\n"
        "## Key Findings\nFinding supported by Sample Case.\n\n"
        "## Supporting Evidence\nEvidence from retrieved sources.\n\n"
        "## Source Analysis\nPrimary source quality is strong.\n\n"
        "## Counterpoints and Alternative Views\nNo major conflicts found.\n\n"
        "## Risks and Limitations\nStandard limitations apply.\n\n"
        "## Research Gaps\nNone identified.\n\n"
        "## Confidence Assessment\nOverall confidence is moderate.\n\n"
        "## References and Citations\n[1] Sample Case\n\n"
        "## Disclaimer\nThis is not legal advice."
    )
    return {
        "research_brief": query,
        "final_report": report,
        "notes": [f"Sample Case discusses {query}."],
        "raw_notes": [],
        "retrieved_sources": sources,
        "source_validations": [],
    }


def main() -> int:
    golden_path = ROOT / "eval" / "golden_queries.jsonl"
    if not golden_path.is_file():
        print(f"Missing golden queries file: {golden_path}")
        return 1

    failures = 0
    for entry in _load_golden(golden_path):
        query = entry["query"]
        state = _synthetic_state(query)
        _claims, metrics, verification = run_post_write_validation(state, {"passed": True})
        min_cov = float(entry.get("min_citation_coverage_pct", 0))
        if metrics.citation_coverage_pct < min_cov:
            print(f"FAIL {query}: citation coverage {metrics.citation_coverage_pct}% < {min_cov}%")
            failures += 1
            continue
        print(
            f"PASS {query}: confidence={metrics.overall_confidence_pct}% "
            f"citation={metrics.citation_coverage_pct}% passed={verification.passed}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
