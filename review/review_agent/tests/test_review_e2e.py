import asyncio
from uuid import UUID

import pytest
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
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


def _apply_llm_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
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
                    contract_quote="total liability of either party",
                    policy_quote="fees paid in the twelve (12) months",
                    rationale="Contract liability cap aligns with policy section on fee limitation.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_classify)
    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_compare)


async def _seed_demo_contract_and_policy(
    client: DocumentMCPClient,
    *,
    tenant: str = "demo",
) -> tuple[str, str]:
    policy_ref = "demo-liability-policy"
    contract_ref = "demo-msa-contract"
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


def _assert_pgvector_retrieval_hits(result: dict) -> None:
    bundles = result.get("section_retrieval_by_id") or {}
    assert bundles, "expected section retrieval bundles"
    hit_sections = [
        sid for sid, raw in bundles.items() if (raw.get("policy_hits") or [])
    ]
    assert hit_sections, "expected at least one section with policy_hits from pgvector"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_document_server_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "document-mcp"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_review_graph_scoped_e2e(monkeypatch):
    _apply_llm_mocks(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        contract_id, policy_id = await _seed_demo_contract_and_policy(client)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_document_id=contract_id,
            contract_title="Vendor MSA",
            policy_document_ids=[policy_id],
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert report.findings
    assert report.metadata.get("pipeline") == "section_first"
    assert result.get("discovered_policy_document_ids") == [policy_id]
    assert result.get("section_retrieval_by_id")
    assert "Limitation of Liability" in report.summary_markdown or report.findings
    _assert_pgvector_retrieval_hits(result)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_reviews_smoke(monkeypatch):
    from review_agent.config import get_settings

    monkeypatch.setenv("CONTRACT_ROUTING_MODE", "lexical")
    monkeypatch.setenv("FINAL_GAP_VERIFY_ENABLED", "false")
    get_settings.cache_clear()
    _apply_llm_mocks(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        contract_id, policy_id = await _seed_demo_contract_and_policy(client)

        async def one_review(i: int):
            return await run_review(
                client=client,
                tenant_id="demo",
                contract_document_id=contract_id,
                contract_title=f"MSA-{i}",
                policy_document_ids=[policy_id],
                contract_type="msa",
                thread_id=f"concurrent-{i}",
            )

        results = await asyncio.gather(*[one_review(i) for i in range(3)])

    assert len(results) == 3
    assert all(r.get("report") and r["report"].findings for r in results)
