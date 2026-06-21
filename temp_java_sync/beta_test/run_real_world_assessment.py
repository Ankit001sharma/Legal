#!/usr/bin/env python3
"""Real-world beta assessment — public company policies + vendor NDA contract."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()

from beta_test.benchmark_score import (  # noqa: E402
    findings_by_section_from_report,
    score_section_expected,
    specs_from_legacy_expected,
)
from document_core.schemas.chunk import DocumentKind, SearchRequest  # noqa: E402
from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402

REAL_WORLD = ROOT / "fixtures" / "real_world"
TENANT = "realworld-public"

# StarTech draft vs Microsoft/Google public enterprise standards
EXPECTED = {
    "1": {
        "topic": "Confidential Information",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": set(),
        "note": "1-year survival vs Microsoft 5-year strict confidence standard",
    },
    "2": {
        "topic": "Term",
        "expect": {"COMPLIANT", "INCONCLUSIVE", "INSUFFICIENT_POLICY_CONTEXT"},
        "bad": set(),
    },
    "3": {
        "topic": "Limitation of Liability",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
        "note": "$50k fixed cap vs MS/Google 12-month fees paid cap",
    },
    "4": {
        "topic": "Indemnification",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
        "note": "Vendor-only indemnity vs mutual MS/Google indemnification",
    },
}


async def sync_real_world(stub: JavaSyncStub) -> dict:
    contract = await stub.sync_contract_from_fixture(REAL_WORLD / "startech_vendor_nda.json")
    policies: list[dict] = []
    for path in sorted((REAL_WORLD / "policies").glob("*.json")):
        policies.append(await stub.sync_policy_from_fixture(path))
    return {"contract": contract, "policies": policies}


async def main() -> int:
    import os

    out = ROOT / "outputs" / "real_world_assessment.json"
    out.parent.mkdir(exist_ok=True)
    os.environ["E2E_TENANT_ID"] = TENANT
    base = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base)
    stub = JavaSyncStub(client, tenant_id=TENANT)
    report_data: dict = {
        "started_at": time.time(),
        "tenant_id": TENANT,
        "test_type": "real_world_public_policies",
        "policy_sources": [],
        "checks": [],
        "score": {},
    }

    def check(name: str, ok: bool, detail: dict) -> None:
        report_data["checks"].append({"name": name, "ok": ok, **detail})
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name}")
        for k, v in detail.items():
            if k != "ok":
                print(f"       {k}: {v}")

    # --- 1. Infrastructure ---
    try:
        health = await client.health()
        caps = list(health.get("capabilities") or [])
        ok = health.get("db") == "ok"
        check(
            "document-mcp health",
            ok,
            {
                "health": health,
                "capabilities": caps,
                "has_search_request_metadata": "search_request_metadata" in caps,
            },
        )
        if not ok:
            return 1
    except Exception as exc:  # noqa: BLE001
        check("document-mcp health", False, {"error": str(exc)})
        return 1

    # --- 2. Sync public-policy playbooks + vendor NDA ---
    try:
        sync = await sync_real_world(stub)
        verify = await stub.verify_contract_indexed(sync["contract"]["document_id"])
        policy_sources = []
        for p in sync["policies"]:
            path = REAL_WORLD / "policies"
            for fp in path.glob("*.json"):
                data = json.loads(fp.read_text(encoding="utf-8"))
                if data.get("policy_ref") == p.get("policy_ref"):
                    meta = data.get("metadata", {})
                    policy_sources.append(
                        {
                            "policy_ref": p.get("policy_ref"),
                            "source_company": meta.get("source_company"),
                            "source_url": meta.get("source_url"),
                        }
                    )
        report_data["policy_sources"] = policy_sources
        ok = verify["section_count"] >= 4 and len(sync["policies"]) >= 5
        check(
            "sync public policies + vendor NDA",
            ok,
            {
                "contract_id": sync["contract"]["document_id"],
                "policies_synced": len(sync["policies"]),
                "sections": verify["section_ids"],
                "sources": policy_sources,
            },
        )
        contract_id = sync["contract"]["document_id"]
    except Exception as exc:  # noqa: BLE001
        check("sync public policies", False, {"error": str(exc)})
        return 1

    # --- 3. Core retrieval ---
    retrieval_ok = False
    hit_count = 0
    retrieval_error = None
    try:
        req = SearchRequest(
            tenant_id=TENANT,
            query="limitation of liability fees paid twelve months",
            kind=DocumentKind.POLICY,
            contract_type="nda",
            top_k=8,
            metadata={"categories": ["liability"]},
        )
        hits = await client.search_policy_by_categories(req, categories=["liability"])
        hit_count = len(hits)
        retrieval_ok = hit_count > 0
        check(
            "retrieval against public liability playbooks",
            retrieval_ok,
            {
                "hits": hit_count,
                "sample_titles": [h.parent_chunk.title for h in hits[:4]],
            },
        )
    except Exception as exc:  # noqa: BLE001
        retrieval_error = str(exc)
        check("retrieval against public liability playbooks", False, {"error": retrieval_error})

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        check("LLM configured", False, {"error": "LLM_API_KEY missing"})
        return 1

    # --- 4. Full review ---
    get_settings.cache_clear()
    os.environ.setdefault("REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID", "true")
    get_settings.cache_clear()

    findings_by_section: dict[str, dict] = {}
    ops: dict = {}
    warnings: list[str] = []
    all_findings: list[dict] = []
    try:
        state = await run_review(
            client=client,
            tenant_id=TENANT,
            contract_document_id=contract_id,
            contract_title="StarTech / CloudVendor NDA (Real-World Beta)",
            contract_type="nda",
        )
        report = state.get("report")
        ok = report is not None
        if report:
            ops = (report.metadata.get("artifact") or {}).get("ops") or {}
            warnings = state.get("warnings") or []
            findings_by_section = findings_by_section_from_report(report)
            for f in report.findings:
                all_findings.append(
                    {
                        "section_id": f.contract_section_id,
                        "status": f.status.value,
                        "severity": f.severity.value,
                        "label": f.dimension_label,
                        "grounded": f.grounded,
                        "policy_title": (f.metadata or {}).get("policy_title", ""),
                        "has_policy_quote": bool(f.policy_quote),
                        "rationale": (f.rationale or "")[:300],
                    }
                )
        check(
            "section-first review completed",
            ok,
            {
                "findings_total": len(report.findings) if report else 0,
                "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
                "ops": ops,
            },
        )
    except Exception as exc:  # noqa: BLE001
        check("section-first review", False, {"error": str(exc)})
        report_data["findings_all"] = all_findings
        out.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        return 1

    # --- 5. Legal accuracy ---
    specs = specs_from_legacy_expected(EXPECTED)
    legal_hits, section_results, legal_score = score_section_expected(findings_by_section, specs)
    legal_total = len(EXPECTED)
    for sr in section_results:
        exp = EXPECTED.get(sr["section"], {})
        sr["expected"] = sorted(exp.get("expect", set()))
        sr["has_policy_quote"] = bool(
            findings_by_section.get(sr["section"], {}).get("policy_title")
        )
        sr["note"] = exp.get("note", "")
    check(
        "legal accuracy vs public enterprise standards",
        legal_hits >= 2,
        {"matched": f"{legal_hits}/{legal_total}", "sections": section_results},
    )

    # --- Scoring ---
    zero_hits = ops.get("retrieval_zero_hit_sections", 4)
    gap_failed = ops.get("gap_llm_failed", 0)
    playbook_compare = ops.get("playbook_compare_count", 0)
    guard_failed = ops.get("guard_failed", 0)
    quote_repair = ops.get("quote_repair_success", 0)
    guard_inference = ops.get("guard_inference_ok", 0)
    has_retrieval_500 = any("500" in w for w in warnings)

    infra_score = 10.0 if health.get("db") == "ok" else 0.0
    retrieval_score = 10.0 if retrieval_ok and zero_hits == 0 else (5.0 if retrieval_ok else 0.0)
    pipeline_score = 10.0 if zero_hits == 0 and gap_failed == 0 else (6.0 if zero_hits < 4 else 3.0)
    legal_score = round(legal_score, 1)
    overall = round(
        infra_score * 0.15 + retrieval_score * 0.35 + pipeline_score * 0.25 + legal_score * 0.25,
        1,
    )

    verdict = (
        "CORE BUG (retrieval)"
        if not retrieval_ok
        else "STALE MCP (restart document-mcp)"
        if has_retrieval_500
        else "REVIEW WEAK (retrieval OK)"
        if zero_hits > 0
        else "HEALTHY"
        if overall >= 7.0
        else "NEEDS TUNING"
    )

    report_data["score"] = {
        "infrastructure": infra_score,
        "retrieval_core": retrieval_score,
        "pipeline_quality": pipeline_score,
        "legal_accuracy": legal_score,
        "overall_10": overall,
        "verdict": verdict,
        "retrieval_zero_hit_sections": zero_hits,
        "playbook_compare_count": playbook_compare,
        "guard_failed": guard_failed,
        "quote_repair_success": quote_repair,
        "guard_inference_ok": guard_inference,
        "ungrounded_count": ops.get("ungrounded_count", 0),
    }
    report_data["findings_by_section"] = findings_by_section
    report_data["findings_all"] = all_findings
    report_data["warnings_sample"] = warnings[:10]
    report_data["elapsed_seconds"] = round(time.time() - report_data["started_at"], 1)

    out.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    print(f"\n=== REAL-WORLD SCORE: {overall}/10 | Verdict: {verdict} ===")
    print(f"Legal accuracy: {legal_hits}/{legal_total} sections matched expectations")
    print(f"Findings: {len(all_findings)} | Playbook compares: {playbook_compare}")
    print(f"Full report: {out}")
    return 0 if overall >= 6.0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
