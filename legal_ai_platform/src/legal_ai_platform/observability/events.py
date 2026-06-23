"""Observability event types."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ObservabilityEvent(BaseModel):
    """Base observability event."""

    event_type: str
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryReceived(ObservabilityEvent):
    """Emitted when the orchestrator receives a user query."""

    event_type: Literal["query_received"] = "query_received"
    query: str = ""
    task_type: str | None = None


class AgentSelected(ObservabilityEvent):
    """Emitted when an agent is selected for a task."""

    event_type: Literal["agent_selected"] = "agent_selected"
    task_type: str = ""
    agent_type: str = ""


class ToolCalled(ObservabilityEvent):
    """Emitted when an MCP tool is invoked."""

    event_type: Literal["tool_called"] = "tool_called"
    tool_name: str = ""
    server: str = ""
    latency_ms: float = 0.0
    success: bool = True


class Latency(ObservabilityEvent):
    """Emitted to record operation latency."""

    event_type: Literal["latency"] = "latency"
    operation: str = ""
    latency_ms: float = 0.0


class Failure(ObservabilityEvent):
    """Emitted when an operation fails."""

    event_type: Literal["failure"] = "failure"
    operation: str = ""
    error: str = ""
    recoverable: bool = False


class ResearchModeSelected(ObservabilityEvent):
    """Emitted when a research mode is selected for a query (analytics telemetry)."""

    event_type: Literal["research_mode_selected"] = "research_mode_selected"
    mode: str = ""
    query_length: int = 0


class ResearchCompleted(ObservabilityEvent):
    """Emitted when a research run finishes, capturing key analytics dimensions."""

    event_type: Literal["research_completed"] = "research_completed"
    mode: str = ""
    retrieval_rounds: int = 0
    citations_found: int = 0
    output_length: int = 0
    latency_ms: float = 0.0
    token_estimate: int = 0
    citation_coverage_pct: float = 0.0
    unsupported_claim_pct: float = 0.0
    hallucination_rate_pct: float = 0.0
    source_quality_score: float = 0.0
    relevance_score: float = 0.0
    coverage_completeness_pct: float = 0.0
    consensus_score: float = 0.0
    overall_confidence_pct: float = 0.0
