#!/usr/bin/env python3
"""Beta assessment — full stack test with realistic NDA fixtures. No main-code edits."""

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

from document_core.schemas.chunk import DocumentKind, SearchRequest  # noqa: E402
from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402

# Expected outcomes for Acme-Vendor NDA vs playbooks (realistic legal review targets)
EXPECTED = {
    "1": {"topic": "Confidentiality", "expect": {"COMPLIANT", "INCONCLUSIVE"}, "bad": set()},
    "2": {"topic": "Term", "expect": {"COMPLIANT", "INCONCLUSIVE"}, "bad": {"NON_COMPLIANT"}},
    "3": {
        "topic": "Limitation of Liability",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
        "note": "$100k cap vs fees-in-12-months playbook",
    },
    "4": {
        "topic": "Indemnification",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
        "bad": {"COMPLIANT"},
        "note": "vendor-only vs mutual indemnity playbook",
    },
}


async def main() -> int:
    import os

    out = ROOT / "outputs" / "beta_assessment.json"
    out.parent.mkdir(exist_ok=True)
    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    base = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base)
    stub = JavaSyncStub(client, tenant_id=tenant)
    report_data: dict = {"started_at": time.time(), "checks": [], "score": {}}

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
        ok = health.get("db") == "ok"
        check("document-mcp health", ok, {"health": health})
        if not ok:
            return 1
    except Exception as exc:  # noqa: BLE001
        check("document-mcp health", False, {"error": str(exc)})
        return 1

    # --- 2. Core retrieval (search_policy_by_categories) ---
    retrieval_ok = False
    hit_count = 0
    retrieval_error = None
    try:
        req = SearchRequest(
            tenant_id=tenant,
            query="limitation of liability cap damages",
            kind=DocumentKind.POLICY,
            contract_type="nda",
            top_k=5,
            metadata={"categories": ["liability"]},
        )
        hits = await client.search_policy_by_categories(req, categories=["liability"])
        hit_count = len(hits)
        retrieval_ok = hit_count > 0
        check(
            "core retrieval (search_policy_by_categories)",
            retrieval_ok,
            {"hits": hit_count, "sample_titles": [h.parent_chunk.title for h in hits[:3]]},
        )
    except Exception as exc:  # noqa: BLE001
        retrieval_error = str(exc)
        check("core retrieval (search_policy_by_categories)", False, {"error": retrieval_error})

    # --- 3. Sync realistic fixtures ---
    try:
        sync = await stub.sync_all_fixtures()
        verify = await stub.verify_contract_indexed(sync["contract"]["document_id"])
        ok = verify["section_count"] >= 4 and len(sync["policies"]) >= 3
        check(
            "java sync (NDA + 3 playbooks)",
            ok,
            {
                "contract_id": sync["contract"]["document_id"],
                "policies": len(sync["policies"]),
                "sections": verify["section_ids"],
            },
        )
        contract_id = sync["contract"]["document_id"]
    except Exception as exc:  # noqa: BLE001
        check("java sync", False, {"error": str(exc)})
        return 1

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
    try:
        state = await run_review(
            client=client,
            tenant_id=tenant,
            contract_document_id=contract_id,
            contract_title="Mutual NDA (Beta Assessment)",
            contract_type="nda",
        )
        report = state.get("report")
        ok = report is not None
        if report:
            ops = (report.metadata.get("artifact") or {}).get("ops") or {}
            warnings = state.get("warnings") or []
            for f in report.findings:
                sid = f.contract_section_id or "?"
                # Keep highest-severity substantive finding per section
                if sid not in findings_by_section or f.severity.value != "info":
                    findings_by_section[sid] = {
                        "status": f.status.value,
                        "severity": f.severity.value,
                        "label": f.dimension_label,
                        "grounded": f.grounded,
                        "has_policy_quote": bool(f.policy_quote),
                        "rationale": (f.rationale or "")[:200],
                    }
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
        return 1

    # --- 5. Legal accuracy vs expected ---
    legal_hits = 0
    legal_total = len(EXPECTED)
    section_results = []
    for sid, exp in EXPECTED.items():
        actual = findings_by_section.get(sid, {})
        status = actual.get("status", "MISSING")
        hit = status in exp["expect"] and status not in exp.get("bad", set())
        if hit:
            legal_hits += 1
        section_results.append(
            {
                "section_id": sid,
                "topic": exp["topic"],
                "expected": sorted(exp["expect"]),
                "actual": status,
                "match": hit,
                "has_policy_quote": actual.get("has_policy_quote", False),
                "note": exp.get("note", ""),
            }
        )
    check(
        "legal accuracy (section outcomes vs playbook expectations)",
        legal_hits >= 2,
        {"matched": f"{legal_hits}/{legal_total}", "sections": section_results},
    )

    # --- Scoring ---
    zero_hits = ops.get("retrieval_zero_hit_sections", 4)
    gap_failed = ops.get("gap_llm_failed", 0)
    playbook_compare = ops.get("playbook_compare_count", 0)
    has_retrieval_500 = any("500" in w for w in warnings)

    infra_score = 10.0 if health.get("db") == "ok" else 0.0
    retrieval_score = 10.0 if retrieval_ok and zero_hits == 0 else (5.0 if retrieval_ok else 0.0)
    pipeline_score = 10.0 if zero_hits == 0 and gap_failed == 0 else (6.0 if zero_hits < 4 else 3.0)
    legal_score = round(10.0 * legal_hits / legal_total, 1)
    overall = round((infra_score * 0.15 + retrieval_score * 0.35 + pipeline_score * 0.25 + legal_score * 0.25), 1)

    core_bug = retrieval_error or has_retrieval_500 or zero_hits == 4
    verdict = (
        "CORE BUG (document_core retrieval)"
        if not retrieval_ok
        else "CONFIG/OPS (document-mcp not restarted after fix)"
        if has_retrieval_500
        else "REVIEW AGENT (retrieval OK but compare weak)"
        if zero_hits > 0
        else "HEALTHY"
    )

    report_data["score"] = {
        "infrastructure": infra_score,
        "retrieval_core": retrieval_score,
        "pipeline_quality": pipeline_score,
        "legal_accuracy": legal_score,
        "overall_10": overall,
        "verdict": verdict,
        "core_python_bug": core_bug,
        "retrieval_zero_hit_sections": zero_hits,
        "playbook_compare_count": playbook_compare,
    }
    report_data["findings_by_section"] = findings_by_section
    report_data["warnings_sample"] = warnings[:8]
    report_data["elapsed_seconds"] = round(time.time() - report_data["started_at"], 1)

    out.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    print(f"\n=== SCORE: {overall}/10 | Verdict: {verdict} ===")
    print(f"Full report: {out}")
    return 0 if overall >= 6.0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
