"""Task classification for routing queries to the correct agent."""

from __future__ import annotations

import re
from typing import Any

# task_type "contract" is an alias for the review agent (compliance review).
TASK_TYPE_ALIASES: dict[str, str] = {
    "contract": "review",
    "compliance": "review",
}


class TaskClassifier:
    """Classify user queries into task types.

    Review routing triggers when:
      - ``task_type`` is review / contract / compliance
      - ``context`` (or top-level review fields) includes contract_text + policies
      - Query matches review intent patterns

    Structured for future LLM-based classification without changing the orchestrator.
    """

    _RULES: list[tuple[str, re.Pattern[str]]] = [
        (
            "review",
            re.compile(
                r"\b("
                r"review|compliance check|check compliance|compare.+policy|"
                r"against (our |the )?policy|policy compliance|non[- ]compliant"
                r")\b",
                re.I,
            ),
        ),
        ("drafting", re.compile(r"\b(draft|write|prepare|generate)\b.*\b(notice|petition|letter|memo)\b", re.I)),
        ("summary", re.compile(r"\b(summarize|summary|summarise)\b", re.I)),
        ("litigation", re.compile(r"\b(litigation|lawsuit|comparable cases|risk)\b", re.I)),
        ("property", re.compile(r"\b(property|real estate|land|lease)\b", re.I)),
        ("ip", re.compile(r"\b(patent|trademark|copyright|intellectual property|IP)\b", re.I)),
        ("translation", re.compile(r"\b(translate|translation)\b", re.I)),
    ]

    DEFAULT_TASK_TYPE = "research"

    def classify(
        self,
        query: str,
        explicit_task_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Return the task type for a query."""
        if explicit_task_type:
            return self.normalize_task_type(explicit_task_type)

        ctx = context or {}
        if ctx.get("contract_text") and ctx.get("policies"):
            return "review"

        for task_type, pattern in self._RULES:
            if pattern.search(query):
                return task_type

        return self.DEFAULT_TASK_TYPE

    @staticmethod
    def normalize_task_type(task_type: str) -> str:
        """Map legacy aliases (e.g. contract) to registered agent task types."""
        normalized = task_type.strip().lower()
        return TASK_TYPE_ALIASES.get(normalized, normalized)
