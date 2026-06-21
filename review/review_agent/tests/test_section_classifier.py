"""Tests for section classifier: lexical-first with LLM fallback (mocked)."""

from uuid import UUID

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from review_agent.config import ReviewSettings
from review_agent.schemas.section_classify import BatchSectionCategoryLLMResult, SectionCategoryResult
from review_agent.services import section_classifier

_LLM_ONLY = ReviewSettings(section_classify_mode="llm_only")


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
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="s1",
                    categories=["liability"],
                    query_terms=["limitation of liability cap"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=_LLM_ONLY)
    assert "liability" in result.categories
    assert result.query_terms


@pytest.mark.asyncio
async def test_lexical_first_skips_llm_liability(monkeypatch):
    section = _section(
        "Limitation of Liability",
        "The total liability shall not exceed fees paid in twelve months.",
    )
    called = {"n": 0}

    async def _fake_invoke(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert called["n"] == 0
    assert "liability" in result.categories
    assert result.classify_warning
    assert result.classify_warning.startswith("lexical_first=title:")
    assert "limitation of liability" in result.query_terms[0].lower()


@pytest.mark.asyncio
async def test_lexical_first_llm_for_definitions(monkeypatch):
    section = _section("Definitions", "Party means the signatory to this Agreement.")

    async def _fake_invoke(_model, _schema, *, system, user):
        assert "Definitions" in user
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="s1",
                    categories=["general"],
                    query_terms=["definitions"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert result.categories == ["general"]


@pytest.mark.asyncio
async def test_lexical_first_batch_mixed(monkeypatch):
    sections = [
        _section("Limitation of Liability", "liability cap text", section_id="3"),
        _section("Definitions", "Party means the signatory.", section_id="9"),
    ]
    called = {"n": 0}

    async def _fake_invoke(_model, _schema, *, system, user):
        called["n"] += 1
        assert "Definitions" in user
        assert "Limitation of Liability" not in user
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="9",
                    categories=["general"],
                    query_terms=["definitions"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    results = await section_classifier.classify_sections_batch(sections)
    assert called["n"] == 1
    assert "liability" in results["3"].categories
    assert results["3"].classify_warning.startswith("lexical_first=")
    assert results["9"].categories == ["general"]


@pytest.mark.asyncio
async def test_llm_only_always_calls_llm(monkeypatch):
    section = _section(
        "Limitation of Liability",
        "The total liability shall not exceed fees paid in twelve months.",
    )
    called = {"n": 0}

    async def _fake_invoke(_model, _schema, *, system, user):
        called["n"] += 1
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="s1",
                    categories=["liability"],
                    query_terms=["limitation of liability cap"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=_LLM_ONLY)
    assert called["n"] == 1
    assert "liability" in result.categories


@pytest.mark.asyncio
async def test_classify_failure_uses_lexical_liability(monkeypatch):
    section = _section(
        "Limitation of Liability",
        "The total liability shall not exceed one hundred thousand dollars.",
    )

    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=_LLM_ONLY)
    assert "liability" in result.categories
    assert result.classify_warning
    assert "lexical_fallback" in result.classify_warning


@pytest.mark.asyncio
async def test_classify_failure_definitions_still_general(monkeypatch):
    section = _section("Definitions", "Party means the signatory.")

    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert result.categories == ["general"]
    assert result.classify_warning


@pytest.mark.asyncio
async def test_classify_all_sections_recovers_failed_batch(monkeypatch):
    sections = [
        _section("Limitation of Liability", "liability cap text", section_id="3"),
        _section("Indemnification", "indemnify text", section_id="4"),
    ]

    async def _boom_batch(*_args, **_kwargs):
        raise RuntimeError("batch classify exploded")

    monkeypatch.setattr(section_classifier, "classify_sections_batch", _boom_batch)

    results = await section_classifier.classify_all_sections(sections, settings=ReviewSettings())
    assert set(results.keys()) == {"3", "4"}
    assert "liability" in results["3"].categories
    assert "indemnity" in results["4"].categories


@pytest.mark.asyncio
async def test_classify_llm_general_enriched_to_minerals(monkeypatch):
    section = _section(
        "Responsible Minerals",
        "Supplier is not obligated to complete Minerals Reporting Templates (MRTs) or RMAP.",
        section_id="3",
    )

    async def _fake_invoke(_model, _schema, *, system, user):
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="3",
                    categories=["general"],
                    query_terms=["general provisions"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=_LLM_ONLY)
    assert "minerals" in result.categories
    assert result.classify_warning
    assert "lexical_enriched" in result.classify_warning


@pytest.mark.asyncio
async def test_lexical_risk_management_title(monkeypatch):
    section = _section(
        "Risk Management and Business Continuity",
        "Supplier is not required to participate in SCV surveys.",
        section_id="6",
    )
    called = {"n": 0}

    async def _fake_invoke(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert called["n"] == 0
    assert "vendor_security" in result.categories


@pytest.mark.asyncio
async def test_lexical_supply_chain_security(monkeypatch):
    section = _section(
        "Supply Chain Security",
        "Supplier is not required to conform to MSS.",
        section_id="5",
    )
    called = {"n": 0}

    async def _fake_invoke(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section)
    assert called["n"] == 0
    assert "security" in result.categories


@pytest.mark.asyncio
async def test_batch_fail_retries_single(monkeypatch):
    sections = [
        _section("Section A", "The parties agree to cooperate in good faith.", section_id="a"),
        _section("Section B", "Notices shall be delivered by certified mail.", section_id="b"),
    ]
    calls = {"n": 0}
    retry_settings = ReviewSettings(
        section_classify_mode="llm_only",
        section_classify_batch_retry_single=True,
    )

    async def _fake_invoke(_model, _schema, *, system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("batch failed")
        if "Section A" in user:
            return BatchSectionCategoryLLMResult(
                items=[
                    SectionCategoryResult(
                        section_id="a",
                        categories=["compliance"],
                        query_terms=["supplier compliance"],
                    )
                ]
            )
        if "Section B" in user:
            return BatchSectionCategoryLLMResult(
                items=[
                    SectionCategoryResult(
                        section_id="b",
                        categories=["termination"],
                        query_terms=["termination notice"],
                    )
                ]
            )
        raise AssertionError(f"unexpected invoke: {user}")

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    results = await section_classifier.classify_sections_batch(sections, settings=retry_settings)


@pytest.mark.asyncio
async def test_llm_general_overridden_by_lexical_title(monkeypatch):
    section = _section(
        "Supply Chain Security",
        "Supplier shall maintain reasonable security practices.",
        section_id="5",
    )

    async def _fake_invoke(_model, _schema, *, system, user):
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id="5",
                    categories=["general"],
                    query_terms=["general provisions"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=_LLM_ONLY)
    assert "security" in result.categories
    assert result.classify_warning
    assert "lexical_enriched" in result.classify_warning
