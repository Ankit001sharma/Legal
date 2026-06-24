"""Section-first LLM compliance compare (production pipeline)."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services.async_limits import gather_limited
from review_agent.services.playbook_context import PlaybookHints, format_playbook_hint_block, hints_from_chunk_metadata
from review_agent.services.quote_validate import truncate_section, validate_and_normalize_quotes
from review_agent.services.token_budget import split_batch_by_token_budget

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "section_compare.md"


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("section_compare.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _hit_lookup(hits_by_section: dict[str, list[RetrievalHit]]) -> dict[str, RetrievalHit]:
    lookup: dict[str, RetrievalHit] = {}
    for sid, hits in hits_by_section.items():
        for hit in hits:
            parent = hit.parent_chunk
            lookup[f"{sid}:{parent.document_id}:{parent.section_id}"] = hit
            lookup[f"{sid}::{parent.section_id}"] = hit
    return lookup


def _backfill_policy_ids(
    item: SectionCompareItem,
    *,
    hits_by_section: dict[str, list[RetrievalHit]],
) -> SectionCompareItem:
    if item.policy_document_id:
        return item
    hits = hits_by_section.get(item.section_id) or []
    if not hits:
        return item
    if item.policy_section_id:
        for hit in hits:
            if hit.parent_chunk.section_id == item.policy_section_id:
                return item.model_copy(
                    update={"policy_document_id": str(hit.parent_chunk.document_id)}
                )
    if len(hits) == 1:
        return item.model_copy(
            update={
                "policy_document_id": str(hits[0].parent_chunk.document_id),
                "policy_section_id": hits[0].parent_chunk.section_id,
            }
        )
    return item


def _format_sections_block(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    max_section_chars: int,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    enrich_playbook: bool = True,
) -> tuple[str, list[str]]:
    blocks: list[str] = []
    truncated_ids: list[str] = []
    hints_map = playbook_hints_by_document or {}
    for section in sections:
        body = section.text or ""
        if len(body.strip()) > max_section_chars:
            truncated_ids.append(section.section_id)
        blocks.append(f"### Contract section: {section.section_id} — {section.title}")
        blocks.append(f"```\n{truncate_section(body, max_section_chars)}\n```")
        policy_hits = hits_by_section.get(section.section_id) or []
        if not policy_hits:
            blocks.append("- **Policies:** [none retrieved]")
            continue
        for idx, hit in enumerate(policy_hits, start=1):
            parent = hit.parent_chunk
            ptext = parent.text or ""
            if len(ptext.strip()) > max_section_chars:
                truncated_ids.append(section.section_id)
            header = (
                f"- **Policy {idx}** doc={parent.document_id} section={parent.section_id} "
                f"title={parent.title}"
            )
            hint_block = ""
            if enrich_playbook:
                hints = hints_map.get(str(parent.document_id))
                if hints is None:
                    hints = hints_from_chunk_metadata(parent.metadata)
                hint_block = format_playbook_hint_block(hints)
            blocks.append(header)
            if hint_block:
                blocks.append(hint_block)
            blocks.append(f"```\n{truncate_section(ptext, max_section_chars)}\n```")
    return "\n\n".join(blocks), truncated_ids


def _failure_items(sections: list[IndexedChunk], *, reason: str) -> list[SectionCompareItem]:
    return [
        SectionCompareItem(
            section_id=section.section_id,
            dimension_label=section.title or section.section_id,
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            rationale=f"Section compare failed: {reason}"[:2000],
        )
        for section in sections
    ]


def _normalize_item_quotes(
    item: SectionCompareItem,
    *,
    section_text: str,
    policy_text: str,
) -> SectionCompareItem:
    adapted = ComplianceLLMResult(
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote=item.policy_quote,
        rationale=item.rationale,
        confidence=item.confidence,
    )
    normalized = validate_and_normalize_quotes(
        adapted,
        contract_text=section_text,
        policy_text=policy_text,
    )
    return item.model_copy(
        update={
            "status": normalized.status,
            "severity": normalized.severity,
            "contract_quote": normalized.contract_quote,
            "policy_quote": normalized.policy_quote,
            "rationale": normalized.rationale,
            "confidence": normalized.confidence,
        }
    )


async def compare_section_batch(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    memory_context: str = "",
    extra_user_context: str = "",
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
) -> tuple[list[SectionCompareItem], list[str]]:
    cfg = settings or get_settings()
    if not sections:
        return [], []

    warnings: list[str] = []
    max_chars = cfg.section_compare_max_section_chars
    system_tpl, user_tpl = _load_prompt_template()
    sections_block, truncated_ids = _format_sections_block(
        sections,
        hits_by_section,
        max_section_chars=max_chars,
        playbook_hints_by_document=playbook_hints_by_document,
        enrich_playbook=cfg.playbook_enrich_compare,
    )
    if truncated_ids:
        unique = sorted(set(truncated_ids))
        warnings.append(
            f"section compare truncated at {max_chars} chars for: {', '.join(unique)}"
        )

    memory_block = ""
    if memory_context.strip():
        memory_block = f"\n\nPrior review context:\n{memory_context.strip()[:4000]}\n"

    user = user_tpl.format(
        contract_type=(contract_type or "unknown").strip() or "unknown",
        sections_block=sections_block + memory_block,
    )
    if extra_user_context.strip():
        user += "\n\n" + extra_user_context.strip()
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.compliance_llm_max_tokens,
    )
    try:
        result = await invoke_structured(
            model,
            BatchSectionCompareLLMResult,
            system=system_tpl,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("section compare LLM failed: %s", exc)
        return _failure_items(sections, reason=str(exc)), warnings

    section_text_by_id = {s.section_id: s.text or "" for s in sections}
    policy_text_by_key: dict[str, str] = {}
    for sid, hits in hits_by_section.items():
        for hit in hits:
            parent = hit.parent_chunk
            key = f"{sid}:{parent.document_id}:{parent.section_id}"
            policy_text_by_key[key] = parent.text or ""

    normalized: list[SectionCompareItem] = []
    for item in result.items:
        item = _backfill_policy_ids(item, hits_by_section=hits_by_section)
        section_text = section_text_by_id.get(item.section_id, "")
        policy_key = f"{item.section_id}:{item.policy_document_id}:{item.policy_section_id}"
        policy_text = policy_text_by_key.get(policy_key, "")
        if item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT) and policy_text:
            item = _normalize_item_quotes(
                item,
                section_text=section_text,
                policy_text=policy_text,
            )
        if not item.dimension_label:
            item = item.model_copy(update={"dimension_label": item.section_id})
        normalized.append(item)
    return normalized, warnings


async def compare_all_sections(
    sections: list[IndexedChunk],
    bundles: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    memory_context: str = "",
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
) -> tuple[list[SectionCompareItem], list[str], dict[str, int]]:
    cfg = settings or get_settings()
    hits_by_section = {s.section_id: bundles.get(s.section_id, []) for s in sections}
    batches = split_batch_by_token_budget(
        sections,
        batch_size=cfg.section_compare_batch_size,
        max_tokens=cfg.section_compare_max_tokens,
        bundles=hits_by_section,
    )

    async def run_batch(batch: list[IndexedChunk]):
        return await compare_section_batch(
            batch,
            hits_by_section,
            contract_type=contract_type,
            memory_context=memory_context,
            settings=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
        )

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    all_items: list[SectionCompareItem] = []
    all_warnings: list[str] = []
    failed_batches = 0
    for result in results:
        if isinstance(result, BaseException):
            failed_batches += 1
            logger.warning("compare batch failed: %s", result)
            continue
        items, warnings = result
        all_items.extend(items)
        all_warnings.extend(warnings)

    stats = {
        "llm_batches_actual": len(batches),
        "llm_batches_failed": failed_batches,
        "sections_truncated": len(
            {w for w in all_warnings if "truncated" in w}
        ),
    }
    return all_items, all_warnings, stats
