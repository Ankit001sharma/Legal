#!/usr/bin/env python3
"""Gold scale assessment — one curated 20-section enterprise MSA × full policy library."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "scale"
TENANT = "scale-gold"

sys.path.insert(0, str(ROOT))
from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()

from beta_test.benchmark_score import (  # noqa: E402
    findings_by_section_from_report,
    load_gold_eval,
    score_gold_gap_sections,
    score_section_expected,
)
from beta_test.scale_corpus import POLICY_LIBRARY, _policy_fixture  # noqa: E402
from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import build_runtime_settings_snapshot, get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402


def _load_contract(*, tenant_id: str) -> dict[str, Any]:
    path = FIXTURES / "enterprise_msa_gold.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tenant_id"] = tenant_id
    return data


def _load_policies() -> list[dict[str, Any]]:
    return [_policy_fixture(item, tenant_id=TENANT) for item in POLICY_LIBRARY]


async def run_gold_benchmark(*, min_gap_hits: int | None = None) -> int:
    import os

    out = ROOT / "outputs" / "scale_gold_assessment.json"
    out.parent.mkdir(exist_ok=True)

    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, eval_meta = load_gold_eval(eval_path)
    contract = _load_contract(tenant_id=TENANT)
    policies = _load_policies()

    base = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base)
    health = await client.health()
    if health.get("db") != "ok":
        print("ERROR: document-mcp not healthy:", health, file=sys.stderr)
        return 1
    print("[OK] document-mcp health")

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: LLM_API_KEY missing", file=sys.stderr)
        return 1

    stub = JavaSyncStub(client, tenant_id=TENANT)
    for policy in policies:
        await stub.sync_policy_from_data(policy)
    contract_result = await stub.sync_contract_from_data(contract)
    verify = await stub.verify_contract_indexed(contract_result["document_id"])
    print(f"[OK] synced gold contract + {len(policies)} policies, {verify['section_count']} sections")
    if verify["section_count"] < len(specs):
        print(
            f"ERROR: expected {len(specs)} indexed sections, got {verify['section_count']}",
            file=sys.stderr,
        )
        return 1

    get_settings.cache_clear()
    runtime_settings = build_runtime_settings_snapshot()

    t0 = time.time()
    state = await run_review(
        client=client,
        tenant_id=TENANT,
        contract_document_id=contract_result["document_id"],
        contract_title=contract["title"],
        contract_type=contract.get("contract_type"),
    )
    elapsed = round(time.time() - t0, 1)
    report = state.get("report")
    if report is None:
        print("ERROR: no report", state.get("warnings"), file=sys.stderr)
        return 1

    findings_map = findings_by_section_from_report(report)
    legal_hits, section_results, score_10 = score_section_expected(findings_map, specs)
    gap_hits, gap_total, gap_results = score_gold_gap_sections(findings_map, specs)

    miss_allowance = int(eval_meta.get("gap_miss_allowance", 2))
    if min_gap_hits is None:
        min_gap_hits = max(1, gap_total - miss_allowance)

    gate_passed = gap_hits >= min_gap_hits
    ops = (report.metadata.get("artifact") or {}).get("ops") or {}

    result = {
        "benchmark_tier": "gold",
        "eval_type": eval_meta.get("eval_type", "engineer_curated_v1"),
        "contract_ref": contract["contract_ref"],
        "tenant_id": TENANT,
        "elapsed_seconds": elapsed,
        "legal_hits": legal_hits,
        "legal_total": len(specs),
        "legal_score_10": score_10,
        "gap_hits": gap_hits,
        "gap_sections": gap_total,
        "gap_miss_allowance": miss_allowance,
        "min_gap_hits_required": min_gap_hits,
        "gate_passed": gate_passed,
        "section_results": section_results,
        "gap_section_results": gap_results,
        "runtime_settings": runtime_settings,
        "ops": ops,
        "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
    }
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\n=== GOLD SCALE: {legal_hits}/{len(specs)} sections | score {score_10}/10 ===")
    print(f"Gap sections: {gap_hits}/{gap_total} (need >= {min_gap_hits}) | Gate: {'PASS' if gate_passed else 'FAIL'}")
    print(f"Elapsed: {elapsed}s | Report: {out}")
    for sr in section_results:
        mark = "OK" if sr["match"] else "MISS"
        print(f"  [{mark}] §{sr['section']} {sr['topic']}: {sr['actual']}")
    return 0 if gate_passed else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run gold enterprise MSA benchmark")
    parser.add_argument(
        "--min-gap-hits",
        type=int,
        default=None,
        help="Minimum gap section hits (default: gap_sections - gap_miss_allowance)",
    )
    args = parser.parse_args()
    return await run_gold_benchmark(min_gap_hits=args.min_gap_hits)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
