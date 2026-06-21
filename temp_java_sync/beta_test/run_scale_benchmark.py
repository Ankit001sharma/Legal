#!/usr/bin/env python3
"""Scale benchmark: 12 large contracts (20 sections) × 42 policies — stress or gold tier."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()

from beta_test.benchmark_score import score_heuristic_gap  # noqa: E402
from beta_test.run_scale_gold import run_gold_benchmark  # noqa: E402
from beta_test.scale_corpus import (  # noqa: E402
    TENANT_PREFIX,
    build_corpus,
    contract_count,
    policy_count,
)
from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import build_runtime_settings_snapshot, get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402
from review_agent.models import llm_gateway  # noqa: E402


async def _sync_tenant(
    stub: JavaSyncStub,
    *,
    contract: dict[str, Any],
    policies: list[dict[str, Any]],
) -> dict[str, Any]:
    tenant = contract["tenant_id"]
    stub.tenant_id = tenant
    for policy in policies:
        policy_copy = {**policy, "tenant_id": tenant}
        await stub.sync_policy_from_data(policy_copy)
    contract_result = await stub.sync_contract_from_data(contract)
    verify = await stub.verify_contract_indexed(contract_result["document_id"])
    return {
        "contract": contract_result,
        "policy_count": len(policies),
        "verify": verify,
    }


async def _run_one(
    client: DocumentMCPClient,
    *,
    contract: dict[str, Any],
    policies: list[dict[str, Any]],
    llm_counter: dict[str, int],
) -> dict[str, Any]:
    tenant = contract["tenant_id"]
    stub = JavaSyncStub(client, tenant_id=tenant)
    sync = await _sync_tenant(stub, contract=contract, policies=policies)

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    calls_before = llm_counter["total"]

    t0 = time.time()
    state = await run_review(
        client=client,
        tenant_id=tenant,
        contract_document_id=sync["contract"]["document_id"],
        contract_title=contract["title"],
        contract_type=contract.get("contract_type"),
    )
    elapsed = round(time.time() - t0, 1)
    llm_calls = llm_counter["total"] - calls_before
    rate_limits = llm_gateway.get_llm_limiter_stats().get("rate_limit_events", 0)

    report = state.get("report")
    findings_by_section: dict[str, dict] = {}
    all_findings: list[dict] = []

    if report:
        for finding in report.findings:
            row = {
                "section_id": finding.contract_section_id,
                "status": finding.status.value,
                "severity": finding.severity.value,
                "label": finding.dimension_label,
                "source": (finding.metadata or {}).get("source", ""),
                "policy_title": (finding.metadata or {}).get("policy_title", ""),
            }
            all_findings.append(row)
            sid = finding.contract_section_id or "?"
            src = row["source"]
            if sid not in findings_by_section or src == "playbook_compare":
                findings_by_section[sid] = row

    eval_labels = (contract.get("metadata") or {}).get("eval_labels") or {}
    scores = score_heuristic_gap(findings_by_section, eval_labels)
    ops = (report.metadata.get("artifact") or {}).get("ops") or {} if report else {}
    stats = dict(state.get("compliance_stats") or {})

    problems: list[str] = []
    if scores["sections_insufficient"] > 0:
        problems.append(f"{scores['sections_insufficient']} section(s) INSUFFICIENT_POLICY_CONTEXT")
    if rate_limits > 0:
        problems.append(f"{rate_limits} rate-limit event(s)")
    if ops.get("llm_batches_failed", 0) > 0:
        problems.append(f"{ops['llm_batches_failed']} compare batch failure(s)")
    group_cap = stats.get("discovery_group_cap_resolved", 6)
    discovery_returned = stats.get("discovery_returned", 0)
    if isinstance(group_cap, int) and discovery_returned > group_cap + 2:
        problems.append(
            f"discovery returned {discovery_returned} policies "
            f"(group cap resolved={group_cap})"
        )
    if scores["false_non_compliant"] > 2:
        problems.append(f"{scores['false_non_compliant']} false NON_COMPLIANT on strong clauses")

    return {
        "tenant_id": tenant,
        "contract_ref": contract["contract_ref"],
        "contract_title": contract["title"],
        "contract_type": contract.get("contract_type"),
        "section_count": sync["verify"]["section_count"],
        "policies_indexed": sync["policy_count"],
        "elapsed_seconds": elapsed,
        "llm_calls": llm_calls,
        "rate_limit_events": rate_limits,
        "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
        "discovery_meta": {
            k: stats.get(k)
            for k in (
                "discovery_returned",
                "discovery_groups",
                "discovery_deduped",
                "discovery_capped",
                "discovery_group_mode",
                "discovery_group_cap_resolved",
                "discovery_max_policies_effective",
            )
        },
        "runtime_settings": stats.get("runtime_settings") or {},
        "findings_total": len(all_findings),
        "scores": scores,
        "ops": ops,
        "problems": problems,
        "warnings_count": len(report.warnings) if report else 0,
    }


async def main() -> int:
    import os

    parser = argparse.ArgumentParser(description="Scale benchmark (stress or gold tier)")
    parser.add_argument("--gold-only", action="store_true", help="Run curated gold contract only")
    parser.add_argument("--gate", action="store_true", help="Enforce quality floors on exit")
    parser.add_argument("--min-avg-gap-recall", type=float, default=65.0)
    parser.add_argument("--min-avg-coverage", type=float, default=70.0)
    parser.add_argument("--min-contracts-ok", type=int, default=12)
    args = parser.parse_args()

    if args.gold_only:
        return await run_gold_benchmark()

    out_dir = ROOT / "outputs" / "scale_benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "scale_benchmark_summary.json"

    contracts, policies = build_corpus()
    print(
        f"Corpus: {contract_count()} contracts × {policy_count()} policies "
        f"(stress tier — heuristic eval_labels)"
    )

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

    llm_counter: dict[str, int] = {"total": 0}
    original_invoke = llm_gateway.invoke_structured

    async def _counting_invoke(*invoke_args: Any, **invoke_kwargs: Any):
        llm_counter["total"] += 1
        return await original_invoke(*invoke_args, **invoke_kwargs)

    llm_gateway.invoke_structured = _counting_invoke  # type: ignore[method-assign]

    get_settings.cache_clear()
    settings = get_settings()
    runtime_settings = build_runtime_settings_snapshot(settings)
    print(
        f"Settings: discovery_group_mode={settings.discovery_group_mode}, "
        f"max_policies={settings.discovery_max_policies}, "
        f"classify_mode={settings.section_classify_mode}, "
        f"compare_hit_mode={settings.compare_policy_hit_mode}"
    )

    results: list[dict[str, Any]] = []
    t_all = time.time()

    for index, contract in enumerate(contracts):
        print(f"\n--- [{index + 1}/{len(contracts)}] {contract['contract_ref']} ---")
        try:
            row = await _run_one(client, contract=contract, policies=policies, llm_counter=llm_counter)
            results.append(row)
            per_path = out_dir / f"assessment_{index:02d}.json"
            per_path.write_text(json.dumps(row, indent=2), encoding="utf-8")
            sc = row["scores"]
            print(
                f"  {row['elapsed_seconds']}s | LLM={row['llm_calls']} | "
                f"discovered={row['discovered_policies']} | "
                f"accuracy={sc['accuracy_score']} | gap_recall={sc['gap_recall_pct']}% | "
                f"coverage={sc['coverage_pct']}%"
            )
            if row["problems"]:
                print(f"  problems: {'; '.join(row['problems'])}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr)
            results.append(
                {
                    "contract_ref": contract["contract_ref"],
                    "error": str(exc),
                    "scores": {"accuracy_score": 0},
                }
            )

    total_elapsed = round(time.time() - t_all, 1)
    ok_results = [r for r in results if "error" not in r]
    avg_accuracy = (
        round(sum(r["scores"]["accuracy_score"] for r in ok_results) / len(ok_results), 1)
        if ok_results
        else 0.0
    )
    avg_time = (
        round(sum(r["elapsed_seconds"] for r in ok_results) / len(ok_results), 1)
        if ok_results
        else 0.0
    )
    total_llm = sum(r.get("llm_calls", 0) for r in ok_results)
    total_rate_limits = sum(r.get("rate_limit_events", 0) for r in ok_results)
    avg_gap_recall = (
        round(sum(r["scores"]["gap_recall_pct"] for r in ok_results) / len(ok_results), 1)
        if ok_results
        else 0.0
    )
    avg_coverage = (
        round(sum(r["scores"]["coverage_pct"] for r in ok_results) / len(ok_results), 1)
        if ok_results
        else 0.0
    )

    gate_passed = True
    if args.gate:
        gate_passed = (
            len(ok_results) >= args.min_contracts_ok
            and avg_gap_recall >= args.min_avg_gap_recall
            and avg_coverage >= args.min_avg_coverage
        )

    summary = {
        "benchmark": "scale_12x42",
        "benchmark_tier": "stress",
        "eval_type": "heuristic_expect_gap",
        "gold_fixture": "fixtures/scale/enterprise_msa_gold.json",
        "gate": {
            "enabled": args.gate,
            "passed": gate_passed,
            "floors": {
                "min_avg_gap_recall": args.min_avg_gap_recall,
                "min_avg_coverage": args.min_avg_coverage,
                "min_contracts_ok": args.min_contracts_ok,
            },
        },
        "contracts_run": len(results),
        "contracts_ok": len(ok_results),
        "policies_per_tenant": policy_count(),
        "sections_per_contract": 20,
        "total_elapsed_seconds": total_elapsed,
        "avg_elapsed_seconds": avg_time,
        "total_llm_calls": total_llm,
        "avg_llm_calls_per_contract": round(total_llm / len(ok_results), 1) if ok_results else 0,
        "total_rate_limit_events": total_rate_limits,
        "avg_accuracy_score": avg_accuracy,
        "avg_gap_recall_pct": avg_gap_recall,
        "avg_coverage_pct": avg_coverage,
        "runtime_settings": runtime_settings,
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n=== SCALE BENCHMARK COMPLETE (stress) ===")
    print(f"Contracts: {len(ok_results)}/{len(results)} OK")
    print(f"Avg accuracy: {avg_accuracy}/100 | Avg gap recall: {avg_gap_recall}%")
    if args.gate:
        print(f"Gate: {'PASS' if gate_passed else 'FAIL'}")
    print(f"Full summary: {summary_path}")

    if args.gate:
        return 0 if gate_passed else 1
    return 0 if len(ok_results) >= 10 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
