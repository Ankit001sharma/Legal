#!/usr/bin/env python3
"""Full E2E: fixture sync + review + tombstone smoke."""

from __future__ import annotations

import asyncio
import json
import sys

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from dev_ui_server import _run_review_for_sync, api_sync, api_tombstone  # noqa: E402
from sync_service import OUTPUTS  # noqa: E402


async def main() -> int:
    root = load_env()
    steps: list[dict[str, object]] = []
    try:
        sync = await api_sync()
        steps.append({"name": "sync", "ok": True})
        await _run_review_for_sync(
            sync,
            contract_title="Mutual NDA (CLI E2E)",
            contract_type="nda",
            use_platform=False,
        )
        steps.append({"name": "review", "ok": True})
        tombstone = await api_tombstone()
        steps.append({"name": "tombstone", "ok": True})
        log = {"steps": steps, "tombstone": tombstone}
        OUTPUTS.mkdir(exist_ok=True)
        (OUTPUTS / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        print("Full E2E passed")
        return 0
    except Exception as exc:  # noqa: BLE001
        steps.append({"name": "failed", "ok": False, "error": str(exc)})
        OUTPUTS.mkdir(exist_ok=True)
        (OUTPUTS / "e2e_log.json").write_text(json.dumps({"steps": steps}, indent=2), encoding="utf-8")
        print(f"E2E failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
