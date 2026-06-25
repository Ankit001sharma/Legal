#!/usr/bin/env python3
"""Local load smoke test for run_review (Phase 32). Mock LLM; real Postgres + document-mcp."""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

# Same path setup as tests/conftest.py
_LEGAL_AI = Path(__file__).resolve().parents[3] / "Legal ai"
if _LEGAL_AI.is_dir() and str(_LEGAL_AI) not in sys.path:
    sys.path.insert(0, str(_LEGAL_AI))

from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.schemas.compliance import ComplianceStatus, Severity
from document_core.schemas.registry import RegisterContractRequest, RegisterPolicyRequest
from document_core.services.registry import stable_contract_document_id, stable_policy_document_id
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.review_graph import run_review
from review_agent.schemas.section_classify import BatchSectionCategoryLLMResult, SectionCategoryResult
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_classifier, section_compare_llm

SAMPLE_CONTRACT = """
12.2 Limitation of Liability
The total liability shall not exceed fees paid in the twelve (12) months preceding the claim.
"""

SAMPLE_POLICY = """
4. Limitation of Liability
Vendor liability shall not exceed the fees paid in the twelve (12) months preceding the claim.
"""


def _install_llm_mocks() -> None:
    async def _fake_classify(_model, schema, *, system, user):
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="12.2",
                    categories=["liability"],
                    query_terms=["limitation of liability"],
                )
            ]
        )

    async def _fake_compare(_model, schema, *, system, user):
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="12.2",
                    dimension_label="Limitation of Liability",
                    status=ComplianceStatus.COMPLIANT,
                    severity=Severity.INFO,
                    rationale="Aligned with indexed vendor policy on liability cap language.",
                    confidence=0.85,
                )
            ]
        )

    section_classifier.get_review_model = lambda **_: object()  # type: ignore[method-assign]
    section_classifier.invoke_structured = _fake_classify  # type: ignore[method-assign]
    section_compare_llm.get_review_model = lambda **_: object()  # type: ignore[method-assign]
    section_compare_llm.invoke_structured = _fake_compare  # type: ignore[method-assign]


async def _seed_demo_contract_and_policy(
    client: DocumentMCPClient,
    *,
    tenant: str,
) -> tuple[str, str]:
    policy_ref = "load-liability-policy"
    contract_ref = "load-msa-contract"
    policy_id = stable_policy_document_id(tenant, policy_ref)
    contract_id = stable_contract_document_id(tenant, contract_ref)

    await client.register_policy(
        RegisterPolicyRequest(
            tenant_id=tenant,
            policy_ref=policy_ref,
            title="Vendor Policy",
            document_id=policy_id,
        )
    )
    await client.register_contract(
        RegisterContractRequest(
            tenant_id=tenant,
            contract_ref=contract_ref,
            title="Vendor MSA",
            document_id=contract_id,
            contract_type="msa",
        )
    )
    policy_result = await client.index_policy(
        IngestRequest(
            tenant_id=tenant,
            document_id=policy_id,
            title="Vendor Policy",
            kind=DocumentKind.POLICY,
            text=SAMPLE_POLICY,
            metadata={"policy_ref": policy_ref},
        )
    )
    contract_result = await client.ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=contract_id,
            title="Vendor MSA",
            kind=DocumentKind.CONTRACT,
            text=SAMPLE_CONTRACT,
            metadata={"contract_ref": contract_ref, "contract_type": "msa"},
        )
    )
    return str(contract_result.document_id), str(policy_result.document_id)


async def _run_load(
    *,
    tenant: str,
    reviews: int,
    concurrency: int,
    max_error_rate: float,
) -> int:
    db_url = os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get("DATABASE_URL"),
    )
    if not db_url:
        print("DATABASE_URL or TEST_DATABASE_URL is required", file=sys.stderr)
        return 2

    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("DOCUMENT_STORE_BACKEND", "pgvector")
    os.environ.setdefault("RERANKER_BACKEND", "lexical")
    os.environ.setdefault("GUARD_PASS_ENABLED", "false")
    os.environ.setdefault("FINAL_GAP_VERIFY_ENABLED", "false")
    os.environ.setdefault("CONTRACT_ROUTING_MODE", "lexical")

    _install_llm_mocks()
    sem = asyncio.Semaphore(max(1, concurrency))
    latencies_ms: list[float] = []
    errors = 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        contract_id, policy_id = await _seed_demo_contract_and_policy(client, tenant=tenant)

        async def one(i: int) -> None:
            nonlocal errors
            async with sem:
                start = time.perf_counter()
                try:
                    result = await run_review(
                        client=client,
                        tenant_id=tenant,
                        contract_document_id=contract_id,
                        contract_title=f"Load-{i}",
                        policy_document_ids=[policy_id],
                        contract_type="msa",
                        thread_id=f"load-{i}",
                    )
                    if not result.get("report"):
                        raise RuntimeError("missing report")
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    print(f"review {i} failed: {exc}", file=sys.stderr)
                else:
                    latencies_ms.append((time.perf_counter() - start) * 1000)

        wall_start = time.perf_counter()
        await asyncio.gather(*[one(i) for i in range(reviews)])
        wall_s = time.perf_counter() - wall_start

    success = reviews - errors
    error_rate = errors / reviews if reviews else 0.0
    p95 = statistics.quantiles(latencies_ms, n=20)[18] if len(latencies_ms) >= 2 else (
        latencies_ms[0] if latencies_ms else 0.0
    )
    print(f"reviews={reviews} success={success} errors={errors} error_rate={error_rate:.1%}")
    print(f"wall_s={wall_s:.2f} p95_ms={p95:.1f}")
    if error_rate > max_error_rate:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load smoke test for review_agent")
    parser.add_argument("--tenant", default="load-test")
    parser.add_argument("--reviews", type=int, default=6)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-error-rate", type=float, default=0.10)
    args = parser.parse_args(argv)
    return asyncio.run(
        _run_load(
            tenant=args.tenant,
            reviews=args.reviews,
            concurrency=args.concurrency,
            max_error_rate=args.max_error_rate,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
