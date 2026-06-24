"""Edge-case unit tests (Phase 32) — no Postgres required."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.services.metadata_at_ingest import resolve_ingest_categories
from review_agent.config import ReviewSettings
from review_agent.schemas.section_classify import BatchSectionCategoryLLMResult, SectionCategoryResult
from review_agent.services import section_classifier


def _section(text: str, *, title: str = "Section", section_id: str = "s1") -> IndexedChunk:
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


def test_unicode_section_parses() -> None:
    text = "1. Confidentiality 🔒\nParty shall protect emoji and unicode: café, naïve."
    tree = parse_text_to_tree(document_id=uuid4(), title="MSA", text=text)
    assert len(tree.sections) >= 1


def test_empty_categories_ingest_resolves() -> None:
    categories, extra = resolve_ingest_categories(
        title="Misc",
        section_texts=["General boilerplate only."],
        provided=[],
        metadata={},
    )
    assert categories == ["general"]
    assert extra.get("auto_tagged") is True


@pytest.mark.asyncio
async def test_long_section_classify_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ReviewSettings(section_classify_mode="llm_only", section_classify_max_chars=12_000)
    long_text = "liability cap " * 4000
    section = _section(long_text, title="Limitation of Liability")
    captured: dict[str, str] = {}

    async def _fake_invoke(_model, _schema, *, system, user):
        captured["user"] = user
        return BatchSectionCategoryLLMResult(
            items=[
                SectionCategoryResult(
                    section_id=section.section_id,
                    categories=["liability"],
                    query_terms=["liability"],
                )
            ]
        )

    monkeypatch.setattr(section_classifier, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_classifier, "invoke_structured", _fake_invoke)

    result = await section_classifier.classify_section_policies(section, settings=settings)
    assert "liability" in result.categories
    assert len(captured["user"]) <= settings.section_classify_max_chars + 500


@pytest.mark.asyncio
async def test_mixed_language_no_crash() -> None:
    text = (
        "1. Haftungsbeschränkung / Limitation of Liability\n"
        "Die Haftung ist auf die in den zwölf Monaten gezahlten Gebühren begrenzt.\n"
        "Total liability shall not exceed fees paid in twelve months."
    )
    tree = parse_text_to_tree(document_id=uuid4(), title="MSA EN/DE", text=text)
    assert len(tree.sections) >= 1
    node = tree.sections[0]
    section = _section(
        node.text or text,
        title=node.title or "Liability",
        section_id=node.section_id,
    )
    result = await section_classifier.classify_section_policies(
        section,
        settings=ReviewSettings(section_classify_mode="lexical_first"),
    )
    assert result.categories
