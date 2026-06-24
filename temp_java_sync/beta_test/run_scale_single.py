#!/usr/bin/env python3
"""Run one scale-benchmark contract (42 policies) for quick stress sample."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from beta_test.run_scale_benchmark import _run_one
from beta_test.scale_corpus import build_corpus
from review_agent.clients.document_client import DocumentMCPClient


async def main() -> None:
    contracts, policies = build_corpus()
    client = DocumentMCPClient("http://localhost:8003")
    llm_counter = {"total": 0}
    row = await _run_one(client, contract=contracts[0], policies=policies, llm_counter=llm_counter)
    out = ROOT / "outputs" / "scale_single_contract.json"
    out.write_text(json.dumps(row, indent=2), encoding="utf-8")
    sc = row["scores"]
    print("SCALE SINGLE:", row["contract_ref"])
    print(
        f"  {row['policies_indexed']} policies | discovered={row['discovered_policies']} | "
        f"{row['elapsed_seconds']}s"
    )
    print(
        f"  accuracy={sc['accuracy_score']} gap_recall={sc['gap_recall_pct']}% "
        f"coverage={sc['coverage_pct']}%"
    )
    print("  saved", out)


if __name__ == "__main__":
    asyncio.run(main())
