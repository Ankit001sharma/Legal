"""Pytest wrapper for temp Java-sync E2E (requires Postgres + pgvector + LLM)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_temp_java_sync_full_e2e():
    from run_full_e2e import main

    code = await main()
    assert code == 0, "run_full_e2e failed — see temp_java_sync/outputs/e2e_log.json"
