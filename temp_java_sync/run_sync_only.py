#!/usr/bin/env python3
"""Sync fixtures to document-mcp only (writes outputs/sync_result.json)."""

from __future__ import annotations

import asyncio
import json
import sys

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from sync_service import save_sync_result, sync_fixture_bundle  # noqa: E402


async def main() -> int:
    import os

    root = load_env()
    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base_url)
    sync = await sync_fixture_bundle(client, tenant_id=tenant)
    path = save_sync_result(sync)
    print(f"Tenant: {tenant}")
    print(f"Contract: {sync['contract']['document_id']} ({sync['verify']['section_count']} sections)")
    print(f"Policies: {len(sync['policies'])}")
    print(f"Wrote {path}")
    print(json.dumps(sync, indent=2)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
