"""Shared benchmark scoring for Cisco, gold, and stress corpora (Phase 22 P6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SectionEvalSpec:
    section_id: str
    topic: str
    expect_statuses: frozenset[str]
    bad_statuses: frozenset[str] = frozenset()
    expect_gap: bool | None = None
    policy_ref_hint: str = ""
    policy_ref_hints: tuple[str, ...] = ()
    required_playbook_category: str = ""
    category: str = ""
    note: str = ""


def specs_from_legacy_expected(
    expected: dict[str, dict[str, Any]],
) -> dict[str, SectionEvalSpec]:
    """Convert Cisco/real-world EXPECTED dict to SectionEvalSpec map."""
    out: dict[str, SectionEvalSpec] = {}
    for sid, exp in expected.items():
        expect = exp.get("expect") or exp.get("expect_statuses") or set()
        bad = exp.get("bad") or exp.get("bad_statuses") or set()
        if isinstance(expect, list):
            expect = set(expect)
        if isinstance(bad, list):
            bad = set(bad)
        out[sid] = SectionEvalSpec(
            section_id=sid,
            topic=str(exp.get("topic", sid)),
            expect_statuses=frozenset(str(s) for s in expect),
            bad_statuses=frozenset(str(s) for s in bad),
            policy_ref_hint=str(exp.get("policy_ref_hint", "")),
            note=str(exp.get("note", "")),
        )
    return out


def load_gold_eval(path: Path) -> tuple[dict[str, SectionEvalSpec], dict[str, Any]]:
    """Load enterprise_msa_eval.json → specs + gate metadata."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    sections = raw.get("sections") or {}
    specs: dict[str, SectionEvalSpec] = {}
    for sid, entry in sections.items():
        expect = entry.get("expect_statuses") or []
        bad = entry.get("bad_statuses") or []
        hints = entry.get("policy_ref_hints") or []
        if entry.get("policy_ref_hint"):
            hints = [entry["policy_ref_hint"], *list(hints)]
        specs[sid] = SectionEvalSpec(
            section_id=str(sid),
            topic=str(entry.get("topic", sid)),
            expect_statuses=frozenset(str(s) for s in expect),
            bad_statuses=frozenset(str(s) for s in bad),
            expect_gap=entry.get("expect_gap"),
            policy_ref_hint=str(entry.get("policy_ref_hint", "")),
            policy_ref_hints=tuple(str(h) for h in hints if h),
            required_playbook_category=str(entry.get("required_playbook_category", "")),
            category=str(entry.get("category", "")),
            note=str(entry.get("note", "")),
        )
    meta: dict[str, Any] = {"eval_type": raw.get("eval_type", "engineer_curated_v1")}
    for key in (
        "contract_ref",
        "gap_sections_minimum_hits",
        "strong_sections_max_false_nc",
        "gap_miss_allowance",
    ):
        if key in raw:
            meta[key] = raw[key]
    return specs, meta


def score_section_expected(
    findings_by_section: dict[str, dict],
    specs: dict[str, SectionEvalSpec],
) -> tuple[int, list[dict[str, Any]], float]:
    """Score section findings against curated expect/bad status sets."""
    hits = 0
    section_results: list[dict[str, Any]] = []
    for sid, spec in sorted(specs.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
        actual = findings_by_section.get(sid, {})
        status = actual.get("status", "MISSING")
        hit = status in spec.expect_statuses and status not in spec.bad_statuses
        if hit:
            hits += 1
        section_results.append(
            {
                "section": sid,
                "topic": spec.topic,
                "actual": status,
                "match": hit,
                "policy": actual.get("policy_title", ""),
                "source": actual.get("source", ""),
                "policy_ref_hint": spec.policy_ref_hint,
            }
        )
    score_10 = round(10.0 * hits / len(specs), 1) if specs else 0.0
    return hits, section_results, score_10


def score_heuristic_gap(
    findings_by_section: dict[str, dict],
    eval_labels: dict[str, dict[str, Any]],
) -> dict[str, float | int]:
    """Stress-corpus heuristic scorer (expect_gap flags — not lawyer-validated)."""
    gap_sections = 0
    gap_hits = 0
    strong_sections = 0
    false_nc = 0
    covered = 0
    insufficient = 0
    total = len(eval_labels)

    for sid, label in eval_labels.items():
        actual = findings_by_section.get(sid, {})
        status = actual.get("status", "MISSING")
        source = actual.get("source", "")

        if status in ("INSUFFICIENT_POLICY_CONTEXT", "MISSING") or not status:
            insufficient += 1
        else:
            covered += 1

        if label.get("expect_gap"):
            gap_sections += 1
            if status in ("NON_COMPLIANT", "INCONCLUSIVE") and source in ("playbook_compare", ""):
                gap_hits += 1
        else:
            strong_sections += 1
            if status == "NON_COMPLIANT" and source == "playbook_compare":
                false_nc += 1

    gap_recall = round(100.0 * gap_hits / gap_sections, 1) if gap_sections else 0.0
    coverage_pct = round(100.0 * covered / total, 1) if total else 0.0
    false_nc_rate = round(100.0 * false_nc / strong_sections, 1) if strong_sections else 0.0
    overall = round(
        0.5 * gap_recall + 0.3 * coverage_pct + 0.2 * max(0.0, 100.0 - false_nc_rate),
        1,
    )

    return {
        "sections_total": total,
        "sections_covered": covered,
        "sections_insufficient": insufficient,
        "coverage_pct": coverage_pct,
        "gap_sections": gap_sections,
        "gap_hits": gap_hits,
        "gap_recall_pct": gap_recall,
        "strong_sections": strong_sections,
        "false_non_compliant": false_nc,
        "false_nc_rate_pct": false_nc_rate,
        "accuracy_score": overall,
    }


def score_gold_gap_sections(
    findings_by_section: dict[str, dict],
    specs: dict[str, SectionEvalSpec],
) -> tuple[int, int, list[dict[str, Any]]]:
    """Count hits on gap-labeled sections only (expect_gap=True)."""
    gap_specs = {sid: spec for sid, spec in specs.items() if spec.expect_gap is True}
    hits, results, _ = score_section_expected(findings_by_section, gap_specs)
    return hits, len(gap_specs), results


def findings_by_section_from_report(report: Any) -> dict[str, dict]:
    """Build section → best finding row from a ReviewReport."""
    findings_by_section: dict[str, dict] = {}
    if report is None:
        return findings_by_section
    for finding in report.findings:
        meta = finding.metadata or {}
        row = {
            "section_id": finding.contract_section_id,
            "status": finding.status.value,
            "severity": finding.severity.value,
            "label": finding.dimension_label,
            "policy_title": meta.get("policy_title", ""),
            "policy_ref": meta.get("policy_ref", ""),
            "policy_categories": list(meta.get("policy_categories") or meta.get("categories") or []),
            "source": meta.get("source", ""),
        }
        sid = finding.contract_section_id or "?"
        src = row["source"]
        if sid not in findings_by_section or src == "playbook_compare":
            findings_by_section[sid] = row
    return findings_by_section


def _scale_policy_category_map() -> dict[str, str]:
    from beta_test.scale_corpus import POLICY_LIBRARY

    mapping: dict[str, str] = {}
    for item in POLICY_LIBRARY:
        cats = item.get("categories") or []
        if cats:
            mapping[str(item["policy_ref"])] = str(cats[0])
    return mapping


def _family_match(
    finding: dict[str, Any],
    spec: SectionEvalSpec,
    *,
    category_map: dict[str, str],
) -> bool:
    required = spec.required_playbook_category or spec.category
    if not required:
        return True

    policy_ref = str(finding.get("policy_ref") or "")
    if policy_ref and category_map.get(policy_ref) == required:
        return True

    categories = finding.get("policy_categories") or []
    if categories and str(categories[0]) == required:
        return True

    hints = spec.policy_ref_hints or ((spec.policy_ref_hint,) if spec.policy_ref_hint else ())
    if policy_ref and any(hint in policy_ref for hint in hints):
        return True

    title = str(finding.get("policy_title") or "").lower()
    if required == "sla" and any(token in title for token in ("sla", "uptime", "availability", "support response")):
        return True
    if required == "ai_usage" and any(token in title for token in ("ai", "automated", "machine learning", "ml ")):
        return True

    return False


def score_playbook_family_alignment(
    findings_by_section: dict[str, dict],
    specs: dict[str, SectionEvalSpec],
    *,
    section_ids: list[str],
    category_map: dict[str, str] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Assert compare/retrieval attached a policy from the expected category family."""
    resolved_map = category_map or _scale_policy_category_map()
    hits = 0
    results: list[dict[str, Any]] = []
    for sid in section_ids:
        spec = specs.get(sid)
        if spec is None:
            continue
        finding = findings_by_section.get(sid, {})
        matched = _family_match(finding, spec, category_map=resolved_map)
        if matched:
            hits += 1
        results.append(
            {
                "section": sid,
                "topic": spec.topic,
                "required_category": spec.required_playbook_category or spec.category,
                "policy": finding.get("policy_title", ""),
                "policy_ref": finding.get("policy_ref", ""),
                "match": matched,
            }
        )
    return hits, results
