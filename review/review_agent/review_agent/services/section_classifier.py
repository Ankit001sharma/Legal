"""Classify contract sections: lexical-first with LLM fallback."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.section_classify import (
    BatchSectionCategoryLLMResult,
    SectionCategoryResult,
)
from review_agent.services.async_limits import gather_limited
from review_agent.services.section_category_lexical import (
    infer_lexical_classify,
    infer_query_terms_from_lexical,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "section_policy_classify.md"
)


def _section_query(section: IndexedChunk) -> str:
    title = (section.title or section.section_id or "").strip()
    body = (section.text or "").strip()
    snippet = " ".join(body.split()[:24])
    if title and snippet:
        return f"{title} {snippet}"
    return title or snippet


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("section_policy_classify.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _enrich_categories_from_lexical(
    categories: list[str],
    section: IndexedChunk,
) -> tuple[list[str], str | None]:
    if categories != ["general"]:
        return categories, None
    lex = infer_lexical_classify(section)
    enriched = normalize_categories(lex.categories) or categories
    if enriched == categories:
        return categories, None
    return enriched, f"lexical_enriched={enriched}"


def _resolve_categories_and_terms(
    section: IndexedChunk,
    *,
    raw_categories: list[str] | None,
    llm_query_terms: list[str] | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Normalize LLM categories; enrich or override general via lexical."""
    categories = normalize_categories(raw_categories or []) or ["general"]
    note: str | None = None

    categories, enrich_note = _enrich_categories_from_lexical(categories, section)
    if enrich_note:
        note = enrich_note

    if categories == ["general"]:
        lex = infer_lexical_classify(section)
        if lex.categories:
            categories = normalize_categories(lex.categories)
            note = f"lexical_override_general={categories}"

    if categories != ["general"]:
        terms = infer_query_terms_from_lexical(categories, section)
    elif llm_query_terms:
        terms = list(llm_query_terms)
    else:
        terms = [_section_query(section)]

    return categories, terms, note


def _lexical_classify_result(
    section: IndexedChunk,
    *,
    settings: ReviewSettings,
) -> SectionCategoryResult | None:
    """Return full result if LLM can be skipped; None if LLM required."""
    if settings.section_classify_mode != "lexical_first":
        return None
    lex = infer_lexical_classify(section)
    if lex.confidence not in ("title", "body") or not lex.categories:
        return None
    if lex.categories == ["general"]:
        return None
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=lex.categories,
        query_terms=infer_query_terms_from_lexical(lex.categories, section),
        classify_warning=f"lexical_first={lex.confidence}:{lex.categories}",
    )


def _fallback_result(
    section: IndexedChunk,
    *,
    reason: str,
    settings: ReviewSettings | None = None,
) -> SectionCategoryResult:
    categories, terms, note = _resolve_categories_and_terms(section, raw_categories=["general"])
    if categories != ["general"]:
        warning = f"{reason}; lexical_fallback={categories}"
    else:
        warning = reason

    logger.warning(
        "section classifier fallback for %s: %s (categories=%s)",
        section.section_id,
        reason,
        categories,
    )
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=categories,
        query_terms=terms,
        classify_warning=warning,
    )


async def classify_section_policies(
    section: IndexedChunk,
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> SectionCategoryResult:
    cfg = settings or get_settings()
    results = await classify_sections_batch(
        [section],
        contract_type=contract_type,
        settings=cfg,
    )
    return results.get(section.section_id) or _fallback_result(
        section,
        reason="missing classify result",
        settings=cfg,
    )


async def _classify_batch_llm(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None,
    settings: ReviewSettings,
) -> dict[str, SectionCategoryResult]:
    if not sections:
        return {}

    system_tpl, _user_tpl = _load_prompt_template()
    blocks: list[str] = []
    for section in sections:
        text = (section.text or "")[: settings.section_classify_max_chars]
        blocks.append(
            f"### Section {section.section_id} — {section.title}\n```\n{text}\n```"
        )
    batch_user = (
        f"Contract type: {contract_type or 'unknown'}\n\n"
        f"Classify each section below. Return one item per section_id.\n\n"
        + "\n\n".join(blocks)
    )
    max_tokens = 512 if len(sections) == 1 else 1024
    model = get_review_model(
        temperature=settings.compliance_llm_temperature,
        max_tokens=max_tokens,
    )
    try:
        result = await invoke_structured(
            model,
            BatchSectionCategoryLLMResult,
            system=system_tpl,
            user=batch_user,
        )
        out: dict[str, SectionCategoryResult] = {}
        for item in result.items:
            section = next(s for s in sections if s.section_id == item.section_id)
            categories, terms, note = _resolve_categories_and_terms(
                section,
                raw_categories=item.categories,
                llm_query_terms=item.query_terms,
            )
            out[item.section_id] = SectionCategoryResult(
                section_id=item.section_id,
                categories=categories,
                query_terms=terms,
                classify_warning=note,
            )
        for section in sections:
            if section.section_id not in out:
                out[section.section_id] = _fallback_result(
                    section,
                    reason="classifier omitted section in batch response",
                    settings=settings,
                )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch section classify failed: %s", exc)
        if not settings.section_classify_batch_retry_single or len(sections) == 1:
            return {
                section.section_id: _fallback_result(
                    section,
                    reason=str(exc) or "batch classify failed",
                    settings=settings,
                )
                for section in sections
            }

        out: dict[str, SectionCategoryResult] = {}
        for section in sections:
            try:
                single = await _classify_batch_llm(
                    [section],
                    contract_type=contract_type,
                    settings=settings,
                )
                out[section.section_id] = single[section.section_id]
            except Exception as single_exc:  # noqa: BLE001
                out[section.section_id] = _fallback_result(
                    section,
                    reason=f"batch_and_single_failed:{single_exc}",
                    settings=settings,
                )
        return out


async def classify_sections_batch(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> dict[str, SectionCategoryResult]:
    cfg = settings or get_settings()
    if not sections:
        return {}

    out: dict[str, SectionCategoryResult] = {}
    needs_llm: list[IndexedChunk] = []

    for section in sections:
        lexical = _lexical_classify_result(section, settings=cfg)
        if lexical is not None:
            out[section.section_id] = lexical
        else:
            needs_llm.append(section)

    if needs_llm:
        out.update(
            await _classify_batch_llm(
                needs_llm,
                contract_type=contract_type,
                settings=cfg,
            )
        )
    return out


async def classify_all_sections(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> dict[str, SectionCategoryResult]:
    cfg = settings or get_settings()
    batch_size = max(1, cfg.section_classify_batch_size)
    batches = [sections[i : i + batch_size] for i in range(0, len(sections), batch_size)]

    async def run_batch(batch: list[IndexedChunk]):
        return await classify_sections_batch(batch, contract_type=contract_type, settings=cfg)

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    merged: dict[str, SectionCategoryResult] = {}
    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("classify batch failed: %s", result)
            for section in batch:
                merged[section.section_id] = _fallback_result(
                    section,
                    reason=str(result),
                    settings=cfg,
                )
            continue
        merged.update(result)
    return merged
