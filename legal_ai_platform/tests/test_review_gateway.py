"""Tests for review routing via the unified orchestrator."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from uuid import uuid4

from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.schemas.compliance import ComplianceStatus, Severity
from document_core.schemas.registry import RegisterContractRequest, RegisterPolicyRequest
from document_core.services.registry import stable_contract_document_id, stable_policy_document_id
from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.agents.review.review_agent import ReviewAgent
from legal_ai_platform.gateway.app import app as gateway_app
from legal_ai_platform.mcp.document_client import DocumentMCPClient
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator, ReviewPayloadError, AgentNotFoundError
from legal_ai_platform.orchestration.registry import AgentRegistry
from mcp.document_server.main import app as document_app
from review_agent.schemas.section_classify import BatchSectionCategoryLLMResult, SectionCategoryResult
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_classifier, section_compare_llm

SAMPLE_CONTRACT = """
12.2 Limitation of Liability
The total liability shall not exceed the fees paid in the twelve (12) months preceding the claim.
"""

SAMPLE_POLICY = """
4. Limitation of Liability
Vendor liability shall not exceed fees paid in the twelve (12) months preceding the claim.
"""


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent="research",
            task_type="research",
            output="research result",
            success=True,
        )


class _MinimalContainer:
    def __init__(self, orchestrator: QueryOrchestrator, registry: AgentRegistry) -> None:
        self.orchestrator = orchestrator
        self.registry = registry
        self.retrieval_client = None
        self.document_client = None
        self.session_service = orchestrator.session_service

    async def shutdown(self) -> None:
        return None


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
                    rationale="Aligned with indexed vendor policy.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_classify)
    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_compare)


async def _seed_contract_and_policy(
    client: DocumentMCPClient,
    *,
    tenant: str = "demo",
) -> tuple[str, str]:
    policy_ref = "gateway-liability-policy"
    contract_ref = "gateway-msa-contract"
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


@pytest.mark.asyncio
async def test_query_endpoint_routes_review(monkeypatch):
    monkeypatch.setenv("CONTRACT_ROUTING_MODE", "lexical")
    monkeypatch.setenv("FINAL_GAP_VERIFY_ENABLED", "false")
    from review_agent.config import get_settings as get_review_settings

    get_review_settings.cache_clear()
    _apply_llm_mocks(monkeypatch)

    doc_transport = ASGITransport(app=document_app)
    async with AsyncClient(transport=doc_transport, base_url="http://doc") as doc_http:
        document_client = DocumentMCPClient("http://doc", http_client=doc_http)
        contract_id, policy_id = await _seed_contract_and_policy(document_client)
        registry = AgentRegistry()
        registry.register("research", _StubResearchAgent())
        registry.register("review", ReviewAgent(document_client=document_client))
        orchestrator = QueryOrchestrator(registry=registry, hooks=HookRegistry())
        gateway_app.state.container = _MinimalContainer(orchestrator, registry)

        gw_transport = ASGITransport(app=gateway_app)
        async with AsyncClient(transport=gw_transport, base_url="http://gw") as gw_http:
            response = await gw_http.post(
                "/query",
                json={
                    "task_type": "review",
                    "tenant_id": "demo",
                    "contract_title": "MSA",
                    "contract_document_id": contract_id,
                    "policy_document_ids": [policy_id],
                    "contract_type": "msa",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "review"
    assert body["success"] is True
    assert "Compliance Review" in body["output"]
    assert body["artifacts"]["report"]["findings"]


@pytest.mark.asyncio
async def test_review_missing_contract_returns_400():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, hooks=HookRegistry())
    gateway_app.state.container = _MinimalContainer(orchestrator, registry)

    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://gw") as http:
        response = await http.post(
            "/query",
            json={
                "task_type": "review",
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_orchestrator_requires_policy_document_ids_for_session_scope():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(
        task_type="review",
        contract_document_id=str(uuid4()),
        policy_source="session",
    )
    with pytest.raises(ReviewPayloadError, match="policy_document_ids"):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_orchestrator_accepts_contract_text_indexed():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(
        task_type="review",
        contract_text="Section 1. Liability shall not exceed fees paid.",
        policy_source="indexed",
    )
    # validation only — no review agent registered; should not raise ReviewPayloadError
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_orchestrator_validates_review_payload():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(task_type="review")
    with pytest.raises(ReviewPayloadError):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_orchestrator_accepts_document_ids_only():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(
        task_type="review",
        contract_document_id=str(uuid4()),
        policy_document_ids=[str(uuid4())],
    )
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_classifier_routes_review_on_document_ids():
    classifier = TaskClassifier()
    task_type = classifier.classify(
        "",
        None,
        {
            "contract_document_id": str(uuid4()),
            "policy_document_ids": [str(uuid4())],
        },
    )
    assert task_type == "review"
