import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.compliance import ComplianceStatus, Severity
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.review_graph import run_review
from review_agent.schemas.section_classify import BatchSectionCategoryLLMResult, SectionCategoryResult
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_classifier, section_compare_llm
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


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
async def test_review_graph_text_e2e(monkeypatch):
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            policy_texts=[{"title": "Vendor Policy", "text": SAMPLE_POLICY, "categories": ["liability"]}],
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert report.findings
    assert report.metadata.get("pipeline") == "section_first"
    assert report.metadata.get("artifact", {}).get("artifact_version") == "1.0"
    assert result.get("section_retrieval_by_id")
    assert "Limitation of Liability" in report.summary_markdown or report.findings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_review_graph_contract_only_discovery(monkeypatch):
    """Contract-only path: pre-indexed tenant policies discovered by routing topics."""
    from review_agent.config import get_settings

    monkeypatch.setenv("CONTRACT_ROUTING_MODE", "lexical")
    get_settings.cache_clear()

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

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_classify)
    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_compare)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        from document_core.schemas.chunk import DocumentKind, IngestRequest

        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
                applies_to_contract_types=["msa"],
                categories=["liability"],
            )
        )
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert result.get("discovered_policy_document_ids")
    assert result.get("contract_routing", {}).get("topics")
    assert report.findings
