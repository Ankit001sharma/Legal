"""Gold scale corpus smoke tests (Phase 22 P6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_LEGAL = Path(__file__).resolve().parents[3]
_TEMP_JAVA = _LEGAL / "temp_java_sync"
if str(_TEMP_JAVA) not in sys.path:
    sys.path.insert(0, str(_TEMP_JAVA))

from beta_test.benchmark_score import load_gold_eval, score_section_expected  # noqa: E402

FIXTURES = _TEMP_JAVA / "fixtures" / "scale"


def test_gold_eval_schema_loads():
    specs, meta = load_gold_eval(FIXTURES / "enterprise_msa_eval.json")
    assert len(specs) == 20
    assert meta.get("contract_ref") == "scale-enterprise-msa-2026"
    assert sum(1 for s in specs.values() if s.expect_gap is True) == 14


def test_gold_scorer_on_fixture_findings():
    specs, _ = load_gold_eval(FIXTURES / "enterprise_msa_eval.json")
    frozen = {
        "2": {"status": "NON_COMPLIANT", "source": "playbook_compare"},
        "3": {"status": "COMPLIANT", "source": "playbook_compare"},
        "6": {"status": "INCONCLUSIVE", "source": "playbook_compare"},
    }
    subset = {sid: specs[sid] for sid in frozen}
    hits, results, score = score_section_expected(frozen, subset)
    assert hits == 3
    assert score == 10.0
    by_section = {r["section"]: r["match"] for r in results}
    assert by_section["2"] is True
    assert by_section["3"] is True
    assert by_section["6"] is True


def test_gold_contract_json_valid():
    raw = json.loads((FIXTURES / "enterprise_msa_gold.json").read_text(encoding="utf-8"))
    assert raw.get("contract_type") == "msa"
    assert raw.get("tenant_id") == "scale-gold"
    assert len(raw.get("sections") or []) == 20
    for section in raw["sections"]:
        assert len((section.get("text") or "").strip()) >= 40
