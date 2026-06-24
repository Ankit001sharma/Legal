"""Tests for section LLM classifier (mocked)."""

from uuid import UUID

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from review_agent.schemas.section_classify import SectionCategoryLLMResult
from review_agent.services import section_classifier


def _section(title: str, text: str, section_id: str = "s1") -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


@pytest.mark.asyncio
async def test_classify_section_llm(monkeypatch):
    section = _section(
        "Limitation of Liability",
        "The total liability shall not exceed fees paid in twelve months.",
    )

    async def _fake_invoke(_model, _schema, *, system, user):
        assert "Limitation of Liability" in user
        return SectionCategoryLLMResult(
            categories=["liability"],
            query_terms=["limitation of liability cap"],
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert "liability" in result.categories
    assert result.query_terms


@pytest.mark.asyncio
async def test_classify_failure_returns_general_with_warning(monkeypatch):
    section = _section("Definitions", "Party means the signatory.")

    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert result.categories == ["general"]
    assert result.classify_warning
