"""Optional one-paragraph report summary — stats only, no contract text."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from document_core.schemas.compliance import ComplianceFinding, ReviewReport
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model
from review_agent.schemas.review_artifact import ReviewArtifact


def _finding_lines(findings: list[ComplianceFinding], limit: int = 12) -> str:
    lines: list[str] = []
    for finding in findings[:limit]:
        lines.append(
            f"- {finding.dimension_label}: {finding.status.value} ({finding.severity.value})"
        )
    if len(findings) > limit:
        lines.append(f"- ... and {len(findings) - limit} more")
    return "\n".join(lines) if lines else "- No findings"


async def maybe_llm_summary_paragraph(
    report: ReviewReport,
    *,
    artifact: ReviewArtifact | None = None,
    settings: ReviewSettings | None = None,
) -> tuple[str, str | None]:
    """Return (paragraph, warning) — empty paragraph when disabled or on failure."""
    cfg = settings or get_settings()
    if not cfg.report_llm_summary:
        return "", None

    ops = artifact.ops if artifact else None
    prompt = (
        "Write 2-4 concise sentences summarizing this compliance review for a lawyer.\n"
        "Use only the structured stats and finding labels below — do not invent facts.\n\n"
        f"Contract: {report.contract_title}\n"
        f"Findings ({len(report.findings)}):\n{_finding_lines(report.findings)}\n"
    )
    if ops:
        prompt += (
            f"\nPipeline: ungrounded={ops.ungrounded_count}, backfill={ops.backfill_count}, "
            f"policy_conflicts={ops.policy_conflict_count}, "
            f"playbook_compare={ops.playbook_compare_count}."
        )

    try:
        model = get_review_model(max_tokens=cfg.report_llm_summary_max_tokens)
        response = await model.ainvoke(
            [
                SystemMessage(
                    content="You summarize legal compliance review results briefly and accurately."
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "")
        paragraph = content.strip() if isinstance(content, str) else ""
        return paragraph, None
    except Exception as exc:  # noqa: BLE001
        return "", f"LLM summary skipped: {exc}"
