"""Tests for section compare LLM (mocked)."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_compare_llm


def _section(section_id: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


def _policy_hit(text: str) -> RetrievalHit:
    doc_id = uuid4()
    chunk = IndexedChunk(
        chunk_id="p1",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="4",
        section_path="4",
        title="Policy",
        text=text,
    )
    return RetrievalHit(parent_chunk=chunk, score=1.0)


@pytest.mark.asyncio
async def test_compare_batch_returns_items(monkeypatch):
    contract_text = "Liability is unlimited for all claims."
    policy_text = "Liability shall not exceed twelve months fees."

    async def _fake_invoke(_model, _schema, *, system, user):
        assert contract_text in user
        assert policy_text in user
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="s1",
                    policy_section_id="4",
                    dimension_label="Liability",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote="Liability is unlimited for all claims.",
                    policy_quote="Liability shall not exceed twelve months fees.",
                    rationale="Contract removes cap required by policy section 4.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", contract_text)
    hits = {"s1": [_policy_hit(policy_text)]}
    items, warnings = await section_compare_llm.compare_section_batch([section], hits)
    assert len(items) == 1
    assert items[0].policy_document_id
    assert items[0].status == ComplianceStatus.NON_COMPLIANT


@pytest.mark.asyncio
async def test_compare_failure_emits_insufficient(monkeypatch):
    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", "Some contract text long enough for review.")
    items, _warnings = await section_compare_llm.compare_section_batch([section], {"s1": [_policy_hit("policy")]})
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
