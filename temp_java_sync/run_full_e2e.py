#!/usr/bin/env python3
"""
Full E2E: Java-sync stub → verify index → prod review → tombstone smoke.

Prerequisites:
  1. Postgres + pgvector running
  2. document-mcp on DOCUMENT_SERVER_URL (default http://localhost:8003)
  3. LLM_API_KEY in temp_java_sync/.env
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402
from review_output import build_review_output_envelope  # noqa: E402


async def main() -> int:
    import os

    root = load_env()
    out_dir = root / "outputs"
    out_dir.mkdir(exist_ok=True)
    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: set LLM_API_KEY in temp_java_sync/.env", file=sys.stderr)
        return 1

    client = DocumentMCPClient(base_url)
    stub = JavaSyncStub(client, tenant_id=tenant)
    log: dict = {"steps": [], "started_at": time.time()}

    def step(name: str, ok: bool, detail: dict | None = None) -> None:
        log["steps"].append({"step": name, "ok": ok, "detail": detail or {}})
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name}")
        if detail:
            for k, v in detail.items():
                print(f"       {k}: {v}")

    # 1 Health
    try:
        health = await stub.health_ok()
        ok = health.get("status") in {"ok", "degraded"} and health.get("db") == "ok"
        step("document-mcp health", ok, health)
        if not ok:
            log["finished"] = False
            (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
            return 1
    except Exception as exc:  # noqa: BLE001
        step("document-mcp health", False, {"error": str(exc)})
        (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        return 1

    # 2 Sync (Java substitute)
    try:
        sync = await stub.sync_all_fixtures()
        verify = await stub.verify_contract_indexed(sync["contract"]["document_id"])
        sync["verify"] = verify
        (out_dir / "sync_result.json").write_text(json.dumps(sync, indent=2), encoding="utf-8")
        ok = verify["section_count"] >= 4 and set(verify["section_ids"]) >= {"1", "3", "4"}
        step("java-sync stub (register + sections[] ingest)", ok, {
            "contract_id": sync["contract"]["document_id"],
            "policies_synced": len(sync["policies"]),
            "sections": verify["section_ids"],
        })
        if not ok:
            (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
            return 1
    except Exception as exc:  # noqa: BLE001
        step("java-sync stub", False, {"error": str(exc)})
        (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        return 1

    # 3 Review (prod path — contract_document_id only)
    get_settings.cache_clear()
    os.environ.setdefault("REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID", "true")
    os.environ.setdefault("REVIEW_REJECT_INLINE_POLICIES", "true")
    get_settings.cache_clear()

    try:
        state = await run_review(
            client=client,
            tenant_id=tenant,
            contract_document_id=sync["contract"]["document_id"],
            contract_title="Mutual NDA (E2E)",
            contract_type="nda",
        )
        report = state.get("report")
        ok = report is not None and report.metadata.get("pipeline") == "section_first"
        detail: dict = {
            "findings": len(report.findings) if report else 0,
            "discovered_policies": len(state.get("discovered_policy_document_ids") or []),
        }
        if report:
            artifact = report.metadata.get("artifact") or {}
            detail["artifact_version"] = artifact.get("artifact_version")
            detail["ops"] = artifact.get("ops")
            review_payload = build_review_output_envelope(
                report=report,
                state=state,
                contract_document_id=sync["contract"]["document_id"],
            )
            (out_dir / "review_result.json").write_text(
                json.dumps(review_payload, indent=2), encoding="utf-8"
            )
        step("section-first review (contract_document_id)", ok, detail)
        if not ok:
            (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
            return 1
    except Exception as exc:  # noqa: BLE001
        step("section-first review", False, {"error": str(exc)})
        (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        return 1

    # 4 Re-sync indemnity policy (restore after tombstone test may have deleted it)
    try:
        from pathlib import Path

        restore = await stub.sync_policy_from_fixture(
            Path(__file__).resolve().parent / "fixtures" / "policies" / "indemnification_standard.json"
        )
        step("restore indemnification policy", True, {"document_id": restore["document_id"]})
    except Exception as exc:  # noqa: BLE001
        step("restore indemnification policy", False, {"error": str(exc)})

    # 5 Tombstone smoke (delete + search exclusion)
    try:
        tombstone = await stub.tombstone_smoke("playbook-indemnification-standard")
        ok = not tombstone["deleted_policy_in_hits"]
        step("delete_policy tombstone", ok, tombstone)
    except Exception as exc:  # noqa: BLE001
        step("delete_policy tombstone", False, {"error": str(exc)})

    log["finished"] = True
    log["elapsed_seconds"] = round(time.time() - log["started_at"], 2)
    (out_dir / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"\nFull log: {out_dir / 'e2e_log.json'}")
    print("E2E complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
