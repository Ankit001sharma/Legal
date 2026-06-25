"""LLM-assisted quote repair before MCP verbatim verification (P2-7)."""

from __future__ import annotations

from pathlib import Path

from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.quote_repair import QuoteRepairResult
from review_agent.services.quote_validate import quote_is_substring, truncate_section

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "quote_repair.md"


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("quote_repair.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


async def repair_quote_for_section(
    *,
    source_text: str,
    candidate_quote: str,
    section_id: str,
    settings: ReviewSettings | None = None,
) -> QuoteRepairResult:
    """Select a verbatim substring from source_text matching candidate_quote."""
    candidate = (candidate_quote or "").strip()
    if not candidate or not (source_text or "").strip():
        return QuoteRepairResult(repair_notes="empty input")

    cfg = settings or get_settings()
    if not cfg.quote_repair_enabled:
        return QuoteRepairResult(repair_notes="quote repair disabled")

    system_tpl, user_tpl = _load_prompt_template()
    truncated = truncate_section(source_text, cfg.quote_repair_max_chars)
    user = user_tpl.format(
        section_id=section_id,
        source_text=truncated,
        candidate_quote=candidate,
    )
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.quote_repair_max_tokens,
    )
    result = await invoke_structured(
        model,
        QuoteRepairResult,
        system=system_tpl,
        user=user,
    )
    repaired = (result.repaired_quote or "").strip()
    if repaired and not quote_is_substring(repaired, source_text):
        return QuoteRepairResult(
            repaired_quote="",
            repair_notes="LLM returned non-substring; rejected",
        )
    return result.model_copy(update={"repaired_quote": repaired})
