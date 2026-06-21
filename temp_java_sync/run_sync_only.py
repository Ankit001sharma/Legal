#!/usr/bin/env python3
"""Sync only — mimics Java background worker (register + structured ingest)."""

from __future__ import annotations

import asyncio
import json
import sys

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402


async def main() -> int:
    import os

    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    client = DocumentMCPClient(base_url)
    stub = JavaSyncStub(client, tenant_id=tenant)

    health = await stub.health_ok()
    if health.get("db") != "ok":
        print("ERROR: document-mcp unhealthy:", health, file=sys.stderr)
        return 1

    result = await stub.sync_all_fixtures()
    verify = await stub.verify_contract_indexed(result["contract"]["document_id"])
    result["verify"] = verify

    print(json.dumps(result, indent=2))
    out_dir = load_env() / "outputs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "sync_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {out_dir / 'sync_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
