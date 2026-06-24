"""Post-grounding rationale guard — LLM checks quote→rationale support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rationale_guard.md"
_GUARD_STATUSES = frozenset(
    {ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT}
)
_QUOTE_CAP = 800


class RationaleGuardResult(BaseModel):
    supported: bool
    reason: str = Field(default="", max_length=500)


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("rationale_guard.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _should_guard(finding: ComplianceFinding) -> bool:
    if finding.status not in _GUARD_STATUSES:
        return False
    meta = finding.metadata or {}
    if meta.get("grounding_failed") or meta.get("guard_failed"):
        return False
    if finding.grounded is not True:
        return False
    if not (finding.contract_quote or finding.policy_quote):
        return False
    if not (finding.rationale or "").strip():
        return False
    return True


def _truncate(text: str, cap: int = _QUOTE_CAP) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= cap else cleaned[: cap - 3] + "..."


async def guard_finding(
    finding: ComplianceFinding,
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
) -> tuple[ComplianceFinding, Literal["checked", "skipped", "failed"]]:
    if not _should_guard(finding):
        return finding, "skipped"

    user = user_tpl.format(
        status=finding.status.value,
        contract_quote=_truncate(finding.contract_quote),
        policy_quote=_truncate(finding.policy_quote),
        rationale=(finding.rationale or "")[:2000],
    )
    result = await invoke_structured(
        model,
        RationaleGuardResult,
        system=system_tpl,
        user=user,
    )
    if result.supported:
        return finding, "checked"

    meta = dict(finding.metadata or {})
    meta["guard_failed"] = True
    meta["prior_status"] = finding.status.value
    if result.reason:
        meta["guard_reason"] = result.reason[:500]
    return finding.model_copy(
        update={
            "status": ComplianceStatus.INCONCLUSIVE,
            "severity": Severity.IMPORTANT,
            "grounded": False,
            "metadata": meta,
        }
    ), "failed"


async def run_guard_pass(
    findings: list[ComplianceFinding],
    *,
    settings: ReviewSettings | None = None,
) -> tuple[list[ComplianceFinding], list[str], dict[str, int]]:
    cfg = settings or get_settings()
    stats = {"guard_checked": 0, "guard_failed": 0, "guard_skipped": 0}
    if not cfg.guard_pass_enabled or cfg.guard_pass_mode != "llm":
        stats["guard_skipped"] = len(findings)
        return findings, [], stats

    to_check = [f for f in findings if _should_guard(f)]
    if not to_check:
        stats["guard_skipped"] = len(findings)
        return findings, [], stats

    system_tpl, user_tpl = _load_prompt_template()
    model = get_review_model(max_tokens=256)
    sem = asyncio.Semaphore(max(1, cfg.guard_pass_concurrency))
    warnings: list[str] = []

    async def _run_one(finding: ComplianceFinding):
        async with sem:
            return await guard_finding(
                finding,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
            )

    outcomes = await asyncio.gather(*[_run_one(f) for f in findings])
    guarded: list[ComplianceFinding] = []
    for finding, (updated, kind) in zip(findings, outcomes, strict=True):
        guarded.append(updated)
        if kind == "skipped":
            stats["guard_skipped"] += 1
        elif kind == "checked":
            stats["guard_checked"] += 1
        elif kind == "failed":
            stats["guard_checked"] += 1
            stats["guard_failed"] += 1
            warnings.append(
                f"finding downgraded to INCONCLUSIVE (guard failed): {finding.dimension_label}"
            )
    return guarded, warnings, stats
