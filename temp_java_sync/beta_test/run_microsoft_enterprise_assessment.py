#!/usr/bin/env python3
"""Microsoft + enterprise playbook assessment — 20-section MSA × ~46 policies."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "scale"
MS_POLICIES = ROOT / "fixtures" / "real_world" / "policies"
TENANT = "microsoft-enterprise"

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
    data = json.loads((FIXTURES / "enterprise_msa_gold.json").read_text(encoding="utf-8"))
    data["tenant_id"] = tenant_id
    data["title"] = "Enterprise Cloud Services Agreement — Contoso / Microsoft Azure Partner"
    meta = dict(data.get("metadata") or {})
    meta["scenario"] = "6+ page vendor MSA vs Microsoft public terms + enterprise playbook library"
    meta["policy_mix"] = "43 enterprise playbooks + 3 Microsoft learn.microsoft.com public policies"
    data["metadata"] = meta
    return data


def _load_policies(*, tenant_id: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    policies = [_policy_fixture(item, tenant_id=tenant_id) for item in POLICY_LIBRARY]
    ms_sources: list[dict[str, str]] = []
    for path in sorted(MS_POLICIES.glob("microsoft_*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["tenant_id"] = tenant_id
        policies.append(raw)
        meta = raw.get("metadata") or {}
        ms_sources.append(
            {
                "policy_ref": raw.get("policy_ref", path.stem),
                "source_company": meta.get("source_company", "Microsoft Corporation"),
                "source_url": meta.get("source_url", ""),
            }
        )
    return policies, ms_sources


async def main() -> int:
    import os

    out = ROOT / "outputs" / "microsoft_enterprise_assessment.json"
    out.parent.mkdir(exist_ok=True)

    eval_path = FIXTURES / "enterprise_msa_eval.json"
    specs, eval_meta = load_gold_eval(eval_path)
    contract = _load_contract(tenant_id=TENANT)
    policies, ms_sources = _load_policies(tenant_id=TENANT)
    section_chars = sum(len(s.get("text") or "") for s in contract.get("sections") or [])

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
    print(
        f"[OK] synced {len(policies)} policies ({len(ms_sources)} Microsoft public + "
        f"{len(policies) - len(ms_sources)} enterprise) + {verify['section_count']} sections "
        f"(~{round(section_chars / 3000, 1)} pages)"
    )

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
    min_gap_hits = max(1, gap_total - miss_allowance)
    gate_passed = gap_hits >= min_gap_hits
    ops = (report.metadata.get("artifact") or {}).get("ops") or {}

    result = {
        "test_type": "microsoft_enterprise_hybrid",
        "benchmark_tier": "gold",
        "contract_ref": contract["contract_ref"],
        "tenant_id": TENANT,
        "policies_indexed": len(policies),
        "microsoft_public_policies": ms_sources,
        "enterprise_playbook_count": len(policies) - len(ms_sources),
        "section_count": verify["section_count"],
        "estimated_pages": round(section_chars / 3000, 1),
        "elapsed_seconds": elapsed,
        "legal_hits": legal_hits,
        "legal_total": len(specs),
        "legal_score_10": score_10,
        "legal_accuracy_pct": round(100.0 * legal_hits / len(specs), 1),
        "gap_hits": gap_hits,
        "gap_sections": gap_total,
        "gap_recall_pct": round(100.0 * gap_hits / gap_total, 1) if gap_total else 0.0,
        "min_gap_hits_required": min_gap_hits,
        "gate_passed": gate_passed,
        "section_results": section_results,
        "gap_section_results": gap_results,
        "runtime_settings": runtime_settings,
        "ops": ops,
        "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
    }
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\n=== MICROSOFT ENTERPRISE: {legal_hits}/{len(specs)} | score {score_10}/10 ===")
    print(f"Gap recall: {gap_hits}/{gap_total} ({result['gap_recall_pct']}%) | Gate: {'PASS' if gate_passed else 'FAIL'}")
    print(f"Discovered: {result['discovered_policies']} | Zero-hit sections: {ops.get('retrieval_zero_hit_sections', '?')}")
    print(f"Elapsed: {elapsed}s | Report: {out}")
    for sr in section_results:
        mark = "OK" if sr["match"] else "MISS"
        print(f"  [{mark}] §{sr['section']} {sr['topic']}: {sr['actual']}")
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
