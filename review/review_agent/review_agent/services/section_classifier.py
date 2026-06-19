"""Classify contract sections into policy category families (LLM only)."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.section_classify import (
    BatchSectionCategoryLLMResult,
    SectionCategoryLLMResult,
    SectionCategoryResult,
)
from review_agent.services.async_limits import gather_limited

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


def _fallback_result(section: IndexedChunk, *, reason: str) -> SectionCategoryResult:
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=["general"],
        query_terms=[_section_query(section)],
        classify_warning=reason,
    )


async def classify_section_policies(
    section: IndexedChunk,
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> SectionCategoryResult:
    results = await classify_sections_batch(
        [section],
        contract_type=contract_type,
        settings=settings,
    )
    return results.get(section.section_id) or _fallback_result(section, reason="missing classify result")


async def classify_sections_batch(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> dict[str, SectionCategoryResult]:
    cfg = settings or get_settings()
    if not sections:
        return {}

    if len(sections) == 1:
        return await _classify_single_llm(sections[0], contract_type=contract_type, settings=cfg)

    system_tpl, user_tpl = _load_prompt_template()
    blocks: list[str] = []
    for section in sections:
        text = (section.text or "")[: cfg.section_classify_max_chars]
        blocks.append(
            f"### Section {section.section_id} — {section.title}\n```\n{text}\n```"
        )
    batch_user = (
        f"Contract type: {contract_type or 'unknown'}\n\n"
        f"Classify each section below. Return one item per section_id.\n\n"
        + "\n\n".join(blocks)
    )
    model = get_review_model(temperature=cfg.compliance_llm_temperature, max_tokens=1024)
    try:
        result = await invoke_structured(
            model,
            BatchSectionCategoryLLMResult,
            system=system_tpl,
            user=batch_user,
        )
        out: dict[str, SectionCategoryResult] = {}
        for item in result.items:
            categories = normalize_categories(item.categories) or ["general"]
            terms = item.query_terms or [_section_query(
                next(s for s in sections if s.section_id == item.section_id)
            )]
            out[item.section_id] = SectionCategoryResult(
                section_id=item.section_id,
                categories=categories,
                query_terms=terms,
            )
        for section in sections:
            if section.section_id not in out:
                out[section.section_id] = _fallback_result(
                    section,
                    reason="classifier omitted section in batch response",
                )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch section classify failed: %s", exc)
        return {
            section.section_id: _fallback_result(section, reason=str(exc))
            for section in sections
        }


async def _classify_single_llm(
    section: IndexedChunk,
    *,
    contract_type: str | None,
    settings: ReviewSettings,
) -> dict[str, SectionCategoryResult]:
    system_tpl, user_tpl = _load_prompt_template()
    user = user_tpl.format(
        contract_type=contract_type or "unknown",
        section_id=section.section_id,
        section_title=section.title or section.section_id,
        section_text=(section.text or "")[: settings.section_classify_max_chars],
    )
    try:
        model = get_review_model(
            temperature=settings.compliance_llm_temperature,
            max_tokens=512,
        )
        result = await invoke_structured(
            model,
            SectionCategoryLLMResult,
            system=system_tpl,
            user=user,
        )
        categories = normalize_categories(result.categories) or ["general"]
        terms = result.query_terms or [_section_query(section)]
        return {
            section.section_id: SectionCategoryResult(
                section_id=section.section_id,
                categories=categories,
                query_terms=terms,
            )
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("section classify LLM failed for %s: %s", section.section_id, exc)
        return {section.section_id: _fallback_result(section, reason=str(exc))}


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
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("classify batch failed: %s", result)
            continue
        merged.update(result)
    return merged
