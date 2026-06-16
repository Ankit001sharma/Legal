"""Shared domain models."""

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.research import ResearchMode, ResearchRequest, ResearchResponse
from legal_ai_platform.models.retrieval import (
    CitationGraphResult,
    FetchResult,
    RetrievalResult,
)

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "CitationGraphResult",
    "FetchResult",
    "ResearchMode",
    "ResearchRequest",
    "ResearchResponse",
    "RetrievalResult",
]
