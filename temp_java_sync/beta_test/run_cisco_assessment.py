#!/usr/bin/env python3
"""Cisco public-policy beta — supplier contract vs Cisco Supplier Guide + HR Policy."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CISCO = ROOT / "fixtures" / "cisco"
TENANT = "cisco-beta"

sys.path.insert(0, str(ROOT))
from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()

from beta_test.benchmark_score import (  # noqa: E402
    findings_by_section_from_report,
    score_section_expected,
    specs_from_legacy_expected,
)
from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402

# Draft contract is intentionally weak vs Cisco public standards
EXPECTED = {
    "1": {
        "topic": "Supplier Code of Conduct (RBA Silver)",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
    "2": {
        "topic": "Human Rights / Forced Labor",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
    "3": {
        "topic": "Responsible Minerals (MRT/RMAP)",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
    "4": {
        "topic": "Environment / CDP / GHG",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
    "5": {
        "topic": "Security (MSS)",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
    "6": {
        "topic": "Risk / SCV / BCP",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
    },
}


async def sync_cisco(stub: JavaSyncStub) -> dict:
    contract = await stub.sync_contract_from_fixture(CISCO / "acme_hardware_supplier_agreement.json")
    policies: list[dict] = []
    for path in sorted((CISCO / "policies").glob("*.json")):
        policies.append(await stub.sync_policy_from_fixture(path))
    verify = await stub.verify_contract_indexed(contract["document_id"])
    return {"contract": contract, "policies": policies, "verify": verify}


async def main() -> int:
    import os

    parser = argparse.ArgumentParser(description="Cisco public-policy assessment")
    parser.add_argument(
        "--min-score",
        type=float,
        default=10.0,
        help="Minimum legal_score_10 to pass (default 10.0 = 6/6 sections)",
    )
    args = parser.parse_args()

    out = ROOT / "outputs" / "cisco_assessment.json"
    out.parent.mkdir(exist_ok=True)
    os.environ["E2E_TENANT_ID"] = TENANT
    base = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base)
    stub = JavaSyncStub(client, tenant_id=TENANT)
    report: dict = {"started_at": time.time(), "tenant_id": TENANT, "test_type": "cisco_public_policies"}

    health = await client.health()
    if health.get("db") != "ok":
        print("ERROR: document-mcp not healthy:", health, file=sys.stderr)
        return 1
    print("[OK] document-mcp health")

    sync = await sync_cisco(stub)
    print(f"[OK] synced contract + {len(sync['policies'])} Cisco policies, {sync['verify']['section_count']} sections")
    contract_id = sync["contract"]["document_id"]

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: LLM_API_KEY missing", file=sys.stderr)
        return 1

    get_settings.cache_clear()
    os.environ.setdefault("REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID", "true")
    get_settings.cache_clear()

    t0 = time.time()
    state = await run_review(
        client=client,
        tenant_id=TENANT,
        contract_document_id=contract_id,
        contract_title="Acme Hardware Supply Agreement (Cisco Beta)",
        contract_type="vendor",
    )
    elapsed = round(time.time() - t0, 1)
    review_report = state.get("report")
    if review_report is None:
        print("ERROR: no report", state.get("warnings"), file=sys.stderr)
        return 1

    findings_by_section = findings_by_section_from_report(review_report)
    all_findings: list[dict] = []
    violations: list[dict] = []

    for f in review_report.findings:
        row = {
            "section_id": f.contract_section_id,
            "status": f.status.value,
            "severity": f.severity.value,
            "label": f.dimension_label,
            "policy_title": (f.metadata or {}).get("policy_title", ""),
            "contract_quote": (f.contract_quote or "")[:400],
            "policy_quote": (f.policy_quote or "")[:400],
            "source": (f.metadata or {}).get("source", ""),
            "rationale": (f.rationale or "")[:300],
        }
        all_findings.append(row)
        if f.status.value == "NON_COMPLIANT" and f.contract_quote and row["source"] != "section_first_final":
            violations.append(row)

    specs = specs_from_legacy_expected(EXPECTED)
    legal_hits, section_results, overall = score_section_expected(findings_by_section, specs)

    ops = (review_report.metadata.get("artifact") or {}).get("ops") or {}
    gate_passed = overall >= args.min_score

    report.update(
        {
            "elapsed_seconds": elapsed,
            "contract_id": contract_id,
            "findings_total": len(all_findings),
            "violations_with_quotes": len(violations),
            "legal_accuracy": f"{legal_hits}/{len(EXPECTED)}",
            "legal_score_10": overall,
            "min_score_required": args.min_score,
            "gate_passed": gate_passed,
            "section_results": section_results,
            "violations": violations,
            "findings_all": all_findings,
            "ops": ops,
            "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
            "summary_excerpt": (review_report.summary_markdown or "")[:2000],
        }
    )
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n=== CISCO BETA: {legal_hits}/{len(EXPECTED)} sections correct | score {overall}/10 ===")
    print(f"Gate: {'PASS' if gate_passed else 'FAIL'} (min {args.min_score})")
    print(f"Findings: {len(all_findings)} | Violations with quotes: {len(violations)}")
    print(f"Discovered policies: {report['discovered_policies']} | Elapsed: {elapsed}s")
    for sr in section_results:
        mark = "OK" if sr["match"] else "MISS"
        print(f"  [{mark}] §{sr['section']} {sr['topic']}: {sr['actual']}")
    print(f"\nFull report: {out}")
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
