"""Research strategy — selects and owns the compiled LangGraph for each mode.

Deep  → current full multi-agent pipeline (unchanged behaviour).
Normal → lightweight single-pass pipeline (2-3 retrieval rounds, concise answer).

The ResearchAgent delegates graph selection to these strategies so the agent
class stays lean and adding a third mode never touches it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph


class BaseResearchStrategy(ABC):
    """Abstract base — owns one compiled LangGraph."""

    @property
    @abstractmethod
    def mode_name(self) -> str:
        """Human-readable mode label (for logging / telemetry)."""

    @property
    @abstractmethod
    def graph(self) -> CompiledStateGraph:
        """The compiled LangGraph to invoke for this mode."""


class DeepResearchStrategy(BaseResearchStrategy):
    """Wraps the existing full-depth research pipeline.

    Behaviour is identical to what was shipped before this PR: multi-agent
    supervisor, bootstrap research, full verification, long-form memo.
    """

    mode_name = "deep"

    def __init__(self) -> None:
        from deep_research_from_scratch.research_agent_full import deep_researcher_builder

        self._graph = deep_researcher_builder.compile(checkpointer=MemorySaver())

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph


class NormalResearchStrategy(BaseResearchStrategy):
    """Lightweight research pipeline: fast answer, 2-3 retrieval rounds.

    Equivalent to ChatGPT normal chat or Perplexity Search — useful for quick
    legal guidance, statute lookups, and conversational follow-ups.
    """

    mode_name = "normal"

    def __init__(self) -> None:
        from deep_research_from_scratch.research_agent_normal import normal_researcher_builder

        self._graph = normal_researcher_builder.compile(checkpointer=MemorySaver())

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph
