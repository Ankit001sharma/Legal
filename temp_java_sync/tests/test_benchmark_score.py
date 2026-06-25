"""Unit tests for benchmark_score (Phase 22 P6)."""

from __future__ import annotations

import json
from pathlib import Path

from beta_test.benchmark_score import (
    load_gold_eval,
    score_heuristic_gap,
    score_section_expected,
    specs_from_legacy_expected,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "scale"


def test_score_section_expected_cisco_pattern():
    expected = {
        "1": {
            "topic": "Security",
            "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
            "bad": {"COMPLIANT"},
        },
        "2": {
            "topic": "Strong",
            "expect": {"COMPLIANT"},
            "bad": set(),
        },
    }
    specs = specs_from_legacy_expected(expected)
    findings = {
        "1": {"status": "NON_COMPLIANT", "source": "playbook_compare"},
        "2": {"status": "COMPLIANT", "source": "playbook_compare"},
    }
    hits, results, score = score_section_expected(findings, specs)
    assert hits == 2
    assert score == 10.0
    assert all(r["match"] for r in results)


def test_score_heuristic_gap_matches_legacy_weights():
    eval_labels = {
        "1": {"expect_gap": True},
        "2": {"expect_gap": True},
        "3": {"expect_gap": False},
    }
    findings = {
        "1": {"status": "NON_COMPLIANT", "source": "playbook_compare"},
        "2": {"status": "INSUFFICIENT_POLICY_CONTEXT", "source": ""},
        "3": {"status": "COMPLIANT", "source": "playbook_compare"},
    }
    scores = score_heuristic_gap(findings, eval_labels)
    assert scores["gap_sections"] == 2
    assert scores["gap_hits"] == 1
    assert scores["coverage_pct"] == 66.7
    assert scores["gap_recall_pct"] == 50.0


def test_gold_eval_schema_loads():
    eval_path = FIXTURES / "enterprise_msa_eval.json"
    assert eval_path.is_file()
    specs, meta = load_gold_eval(eval_path)
    assert len(specs) == 20
    assert meta.get("gap_sections_minimum_hits") == 12
    gap_count = sum(1 for s in specs.values() if s.expect_gap is True)
    assert gap_count == 14


def test_gold_scorer_gap_section_hit():
    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, _ = load_gold_eval(eval_path)
    findings = {
        "2": {"status": "NON_COMPLIANT", "source": "playbook_compare", "policy_title": "RBA"},
    }
    hits, results, _ = score_section_expected(findings, {"2": specs["2"]})
    assert hits == 1
    assert results[0]["match"] is True


def test_gold_scorer_bad_compliant_on_gap():
    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, _ = load_gold_eval(eval_path)
    # Section 2 is a gap section (expect_gap=True)
    findings = {
        "2": {"status": "COMPLIANT", "source": "playbook_compare"},
    }
    hits, results, _ = score_section_expected(findings, {"2": specs["2"]})
    assert hits == 0
    assert results[0]["match"] is False


def test_gold_contract_fixture_has_twenty_sections():
    contract_path = FIXTURES / "enterprise_msa_gold.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["contract_ref"] == "scale-enterprise-msa-2026"
    assert len(contract["sections"]) == 20


def test_playbook_family_alignment_sla_section():
    from beta_test.benchmark_score import load_gold_eval, score_playbook_family_alignment

    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, _ = load_gold_eval(eval_path)
    findings = {
        "13": {
            "status": "INSUFFICIENT_POLICY_CONTEXT",
            "policy_ref": "playbook-sla-availability",
            "policy_categories": ["sla"],
            "policy_title": "SLA Availability Standard",
        }
    }
    hits, results = score_playbook_family_alignment(findings, specs, section_ids=["13"])
    assert hits == 1
    assert results[0]["match"] is True


def test_playbook_family_alignment_rejects_wrong_family():
    from beta_test.benchmark_score import load_gold_eval, score_playbook_family_alignment

    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, _ = load_gold_eval(eval_path)
    findings = {
        "13": {
            "status": "INSUFFICIENT_POLICY_CONTEXT",
            "policy_ref": "playbook-confidentiality-survival",
            "policy_categories": ["confidentiality"],
            "policy_title": "Confidentiality Survival",
        }
    }
    hits, results = score_playbook_family_alignment(findings, specs, section_ids=["13"])
    assert hits == 0
    assert results[0]["match"] is False
