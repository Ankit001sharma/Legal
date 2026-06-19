"""Shared domain models."""

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.task_types import TaskType
from legal_ai_platform.models.research import ResearchMode, ResearchRequest, ResearchResponse
from legal_ai_platform.models.retrieval import (
    CitationGraphResult,
    FetchResult,
    RetrievalResult,
)

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "TaskType",
    "CitationGraphResult",
    "FetchResult",
    "ResearchMode",
    "ResearchRequest",
    "ResearchResponse",
    "RetrievalResult",
]
