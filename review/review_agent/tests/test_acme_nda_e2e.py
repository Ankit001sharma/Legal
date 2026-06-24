"""Phase 38.7 — Acme NDA scoped review with liability + indemnity policies."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestSectionInput
from document_core.schemas.compliance import ComplianceStatus, Severity
from document_core.schemas.registry import RegisterContractRequest, RegisterPolicyRequest
from document_core.services.registry import stable_contract_document_id, stable_policy_document_id
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.review_graph import run_review
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_compare_llm
from tests.acme_fixtures import acme_contract_sections, acme_policy_specs, load_acme_contract

ACME_TENANT = "acme-nda-test"


def _apply_compare_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_compare(_model, schema, *, system, user):
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="6",
                    dimension_label="Limitation of Liability",
                    status=ComplianceStatus.COMPLIANT,
                    severity=Severity.INFO,
                    contract_quote="aggregate liability",
                    policy_quote="aggregate liability",
                    rationale="Liability cap aligns with enterprise MDSA position.",
                    confidence=0.9,
                ),
                SectionCompareItem(
                    section_id="7",
                    dimension_label="Defense and Indemnification",
                    status=ComplianceStatus.COMPLIANT,
                    severity=Severity.INFO,
                    contract_quote="defend, indemnify",
                    policy_quote="defend you against",
                    rationale="Mutual indemnification structure matches policy.",
                    confidence=0.9,
                ),
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_compare)


async def _seed_acme_nda(
    client: DocumentMCPClient,
    *,
    tenant: str = ACME_TENANT,
) -> tuple[str, str, str]:
    contract_data = load_acme_contract()
    policies = acme_policy_specs()
    liability = policies["ms_liability"]
    indemnity = policies["ms_indemnity"]

    liability_id = stable_policy_document_id(tenant, liability["policy_ref"])
    indemnity_id = stable_policy_document_id(tenant, indemnity["policy_ref"])
    contract_id = stable_contract_document_id(tenant, contract_data["contract_ref"])

    await client.register_policy(
        RegisterPolicyRequest(
            tenant_id=tenant,
            policy_ref=liability["policy_ref"],
            title=liability["title"],
            document_id=liability_id,
        )
    )
    await client.register_policy(
        RegisterPolicyRequest(
            tenant_id=tenant,
            policy_ref=indemnity["policy_ref"],
            title=indemnity["title"],
            document_id=indemnity_id,
        )
    )
    await client.register_contract(
        RegisterContractRequest(
            tenant_id=tenant,
            contract_ref=contract_data["contract_ref"],
            title=contract_data["title"],
            document_id=contract_id,
            contract_type=contract_data.get("contract_type", "nda"),
        )
    )

    for policy_data, policy_id in (
        (liability, liability_id),
        (indemnity, indemnity_id),
    ):
        await client.index_policy(
            IngestRequest(
                tenant_id=tenant,
                document_id=policy_id,
                title=policy_data["title"],
                kind=DocumentKind.POLICY,
                policy_type=policy_data.get("policy_type"),
                sections=[
                    IngestSectionInput(
                        section_id=str(section["section_id"]),
                        title=str(section.get("title") or ""),
                        text=str(section["text"]),
                    )
                    for section in policy_data["sections"]
                ],
                metadata={"policy_ref": policy_data["policy_ref"], **policy_data.get("metadata", {})},
            )
        )

    contract_result = await client.ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=contract_id,
            title=contract_data["title"],
            kind=DocumentKind.CONTRACT,
            sections=acme_contract_sections(),
            metadata={
                "contract_ref": contract_data["contract_ref"],
                "contract_type": contract_data.get("contract_type", "nda"),
            },
        )
    )
    return (
        str(contract_result.document_id),
        str(liability_id),
        str(indemnity_id),
    )


def _top_hit_categories(bundle: dict) -> list[str]:
    hits = bundle.get("policy_hits") or []
    if not hits:
        return []
    parent = hits[0].get("parent_chunk") or {}
    meta = parent.get("metadata") or {}
    cats = meta.get("categories") or []
    if cats:
        return [str(c) for c in cats]
    return list(bundle.get("categories") or [])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_acme_nda_scoped_review_e2e(monkeypatch):
    monkeypatch.setenv("FINAL_GAP_VERIFY_ENABLED", "false")
    monkeypatch.setenv("CONTRACT_ROUTING_MODE", "lexical")
    _apply_compare_mock(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        contract_id, liability_id, indemnity_id = await _seed_acme_nda(client)
        result = await run_review(
            client=client,
            tenant_id=ACME_TENANT,
            contract_document_id=contract_id,
            contract_title=load_acme_contract()["title"],
            policy_document_ids=[liability_id, indemnity_id],
            contract_type="nda",
        )

    stats = result.get("compliance_stats") or {}
    assert stats.get("retrieval_zero_hit_sections", -1) == 0

    bundles = result.get("section_retrieval_by_id") or {}
    assert "6" in bundles and "7" in bundles
    assert bundles["6"].get("policy_hits"), "section 6 expected liability policy hits"
    assert bundles["7"].get("policy_hits"), "section 7 expected indemnity policy hits"
    assert "liability" in _top_hit_categories(bundles["6"])
    assert "indemnity" in _top_hit_categories(bundles["7"])

    discovered = list(result.get("discovered_policy_document_ids") or [])
    assert len(discovered) == 2
    assert set(discovered) == {liability_id, indemnity_id}

    report = result["report"]
    assert report is not None
    artifact = (report.metadata or {}).get("artifact") or {}
    ops = artifact.get("ops") or {}
    assert ops.get("retrieval_zero_hit_sections", -1) == 0
    assert report.findings or ops.get("playbook_compare_count", 0) >= 1
