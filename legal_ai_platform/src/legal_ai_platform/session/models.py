"""Platform-owned session models (shared across all agents)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Turn(BaseModel):
    """One message in the unified session transcript."""

    role: Literal["user", "assistant"]
    content: str
    agent: str | None = None
    task_type: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class MatterSnapshot(BaseModel):
    """Cross-agent handoff: contract, policies, last review report."""

    contract_text: str | None = None
    contract_document_id: str | None = None
    contract_title: str | None = None
    policies: list[dict[str, Any]] = Field(default_factory=list)
    contract_type: str | None = None
    policy_type: str | None = None
    last_review_report: dict[str, Any] | None = None
    last_agent: str | None = None
    last_task_type: str | None = None


class SessionState(BaseModel):
    """Full session state for one thread_id."""

    thread_id: str
    tenant_id: str
    summary: str = ""
    turns: list[Turn] = Field(default_factory=list)
    matter: MatterSnapshot = Field(default_factory=MatterSnapshot)

    def recent_turns(self, limit: int = 20) -> list[Turn]:
        return self.turns[-limit:]

    def to_context_dict(self, *, transcript_limit: int = 20) -> dict[str, Any]:
        """Injected into AgentRequest.context['session'] — agents may read, not required."""
        return {
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "summary": self.summary,
            "transcript_recent": [
                t.model_dump(mode="json") for t in self.recent_turns(transcript_limit)
            ],
            "matter": self.matter.model_dump(mode="json"),
        }
