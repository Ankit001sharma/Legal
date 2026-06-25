"""LLM rationale rewrite when guard returns UNSUPPORTED (P2-6)."""

from __future__ import annotations

from pathlib import Path

from document_core.schemas.compliance import ComplianceFinding
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.guard_llm import RationaleRepairResult

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rationale_repair.md"
_QUOTE_CAP = 800


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("rationale_repair.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _truncate(text: str, cap: int = _QUOTE_CAP) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= cap else cleaned[: cap - 3] + "..."


async def repair_rationale_for_finding(
    finding: ComplianceFinding,
    *,
    settings: ReviewSettings | None = None,
) -> str:
    cfg = settings or get_settings()
    system_tpl, user_tpl = _load_prompt_template()
    user = user_tpl.format(
        status=finding.status.value,
        dimension_label=finding.dimension_label or "",
        contract_quote=_truncate(finding.contract_quote),
        policy_quote=_truncate(finding.policy_quote),
        rationale=(finding.rationale or "")[:2000],
    )
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.guard_pass_max_tokens,
    )
    result = await invoke_structured(
        model,
        RationaleRepairResult,
        system=system_tpl,
        user=user,
    )
    return result.rationale.strip()
