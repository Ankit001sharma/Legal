"""Research-specific request/response models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from legal_ai_platform.models.retrieval import RetrievalResult


class ResearchMode(str, Enum):
    """Research depth mode.

    NORMAL — fast legal answer with 2-3 retrieval rounds (default).
    DEEP   — exhaustive research memo with full authority discovery (current pipeline).
    """

    NORMAL = "normal"
    DEEP = "deep"


class ResearchRequest(BaseModel):
    """Input for the Research Agent."""

    query: str
    mode: ResearchMode = ResearchMode.NORMAL
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    session_id: str


class ResearchResponse(BaseModel):
    """Output from the Research Agent."""

    report: str = ""
    research_brief: str | None = None
    sources: list[RetrievalResult] = Field(default_factory=list)
    raw_notes: list[str] = Field(default_factory=list)
    verification: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)
    awaiting_input: bool = False
