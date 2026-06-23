"""Observability package."""

from legal_ai_platform.observability.events import (
    AgentSelected,
    Failure,
    Latency,
    ObservabilityEvent,
    QueryReceived,
    ResearchCompleted,
    ResearchModeSelected,
    ToolCalled,
)
from legal_ai_platform.observability.hooks import HookRegistry, LoggingHook, ObservabilityHook
from legal_ai_platform.observability.logging_setup import (
    configure_logging,
    get_logger,
    sanitize_for_log,
    truncate,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "sanitize_for_log",
    "truncate",
    "AgentSelected",
    "Failure",
    "HookRegistry",
    "Latency",
    "LoggingHook",
    "ObservabilityEvent",
    "ObservabilityHook",
    "QueryReceived",
    "ResearchCompleted",
    "ResearchModeSelected",
    "ToolCalled",
]
