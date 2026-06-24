#!/usr/bin/env python3
"""E2E test: Xecurify policies + NDA contract (direct + platform review)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "xecurify_e2e.json"


async def main() -> int:
    if not FIXTURE.is_file():
        print(f"Missing fixture: {FIXTURE}", file=sys.stderr)
        return 1

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = data["contract_text"]
    tenant = data.get("tenant_id", "e2e-demo")

    base = "http://localhost:8090"
    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as http:
        health = await http.get(f"{base}/api/health")
        print("health:", health.status_code, health.json().get("document_mcp", {}).get("db"))

        sync_body = {
            "policies": policies,
            "use_shared_tenant": True,
            "replace_tenant_policies": True,
        }
        print(f"\n=== Sync {len(policies)} policies (tenant={tenant}) ===")
        sync_r = await http.post(f"{base}/api/sync-policies", json=sync_body)
        print("sync status:", sync_r.status_code)
        if sync_r.status_code >= 400:
            print(sync_r.text[:2000])
            return 1
        sync = sync_r.json()
        for p in sync.get("policies", []):
            print(f"  - {p.get('title', p.get('policy_ref'))}: {p.get('categories', [])} tagger={p.get('tagger')}")

        review_body = {
            "query": "Review this mutual NDA against our Code of Conduct, data retention, security, and privacy policies",
            "contract_text": contract_text,
            "contract_title": "Mutual NDA - Xecurify / Recipient",
            "contract_type": "nda",
            "tenant_id": tenant,
        }

        for label, use_platform in [("DIRECT", False), ("PLATFORM", True)]:
            body = {**review_body, "use_platform": use_platform}
            print(f"\n=== Review ({label}) ===")
            r = await http.post(f"{base}/api/review-text", json=body)
            print("status:", r.status_code)
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail", r.text)
                except Exception:
                    detail = r.text
                print("error:", str(detail)[:1500])
                continue
            out = r.json()
            findings = out.get("findings") or []
            violations = [
                f
                for f in findings
                if f.get("status") == "NON_COMPLIANT"
            ]
            print(f"findings: {len(findings)} | non-compliant: {len(violations)}")
            print("summary:", (out.get("summary_markdown") or out.get("output") or "")[:800])
            for f in violations[:5]:
                print(
                    f"  [{f.get('contract_section_id')}] {f.get('dimension_label')}: "
                    f"{(f.get('rationale') or '')[:120]}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
