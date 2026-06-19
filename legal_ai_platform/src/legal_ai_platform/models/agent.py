"""Generic agent request/response envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from legal_ai_platform.models.research import ResearchMode
from legal_ai_platform.models.task_types import TaskType


class AgentRequest(BaseModel):
    """Generic task envelope sent to any agent via the orchestrator."""

    query: str
    task_type: list[TaskType] | None = Field(
        default=None,
        description=(
            "Optional agent task type(s) in priority order "
            "(e.g. contract review, summarization, research). "
            "If omitted, the classifier decides."
        ),
        examples=[["research"], ["contract"], ["summary", "research"]],
    )
    mode: ResearchMode = Field(
        default=ResearchMode.NORMAL,
        description="Research depth mode: 'normal' (fast, default) or 'deep' (exhaustive memo)",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = Field(
        default=None,
        description="Super-admin only: operate inside a tenant context. Ignored for other roles.",
    )
    user_id: str | None = Field(
        default=None,
        description="Set by gateway from JWT; not trusted from client.",
    )
    role: str | None = Field(
        default=None,
        description="Set by gateway from JWT; not trusted from client.",
    )
    max_results: int = Field(default=10, ge=1, le=100)
    session_id: str | None = Field(
        default=None,
        description=(
            "Frontend-owned conversation session id. "
            "Reuse the same value on every turn; null for anonymous guests."
        ),
    )


class AgentResponse(BaseModel):
    """Generic response envelope returned by any agent."""

    agent: str
    task_type: str
    output: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    success: bool = True
    session_id: str | None = Field(
        default=None,
        description="Echo of the request session_id (same value, never a newly generated id)",
    )
    awaiting_input: bool = Field(
        default=False,
        description="True when the agent needs a follow-up reply (e.g. a clarification)",
    )
    research_directions: list[str] = Field(
        default_factory=list,
        description="Pre-research direction options for the user to choose from; non-empty when awaiting_input=True and the agent is presenting research angles",
    )
