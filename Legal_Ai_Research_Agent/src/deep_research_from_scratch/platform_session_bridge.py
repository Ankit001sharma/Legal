"""Bridge for legal_ai_platform unified session (transcript + summary).

When the platform gateway owns the session, research graphs read injected session
context and skip writing duplicate JSONL transcripts. Standalone research runs
(without platform) are unchanged.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_platform_session: ContextVar[dict[str, Any] | None] = ContextVar(
    "platform_session", default=None
)


def activate_platform_session(session_block: dict[str, Any]) -> None:
    """Mark this request as platform-session-owned (call before graph.ainvoke)."""
    _platform_session.set(session_block)


def clear_platform_session() -> None:
    """Reset platform session bridge after graph.ainvoke."""
    _platform_session.set(None)


def get_platform_session() -> dict[str, Any] | None:
    return _platform_session.get()


def platform_owns_session() -> bool:
    block = get_platform_session()
    return bool(block and block.get("platform_owns_session"))


def format_platform_session_context(session_block: dict[str, Any]) -> str:
    """Format unified platform transcript for load_memory injection."""
    summary = (session_block.get("summary") or "").strip()
    turns = session_block.get("transcript_recent") or []
    matter = session_block.get("matter") or {}

    lines = ["Unified platform session transcript:"]
    if summary:
        lines.extend(["", f"Rolling summary: {summary}"])

    last_agent = matter.get("last_agent")
    last_task = matter.get("last_task_type")
    if last_agent or last_task:
        lines.append(f"Last turn: agent={last_agent or 'n/a'}, task={last_task or 'n/a'}")

    if matter.get("last_review_report"):
        lines.append("(Prior contract review report is available in this session.)")

    lines.append("")
    if not turns:
        lines.append("(No prior turns in this session.)")
    else:
        for turn in turns:
            role = turn.get("agent") or turn.get("role", "user")
            content = (turn.get("content") or "").strip()
            if content:
                lines.append(f"[{role}] {content[:800]}")

    return "\n".join(lines)
