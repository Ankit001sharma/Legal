"""Review pipeline error taxonomy (Phase 29).

Hierarchy:
    RecoverableError        — retry or degrade gracefully
      └─ LLMUnavailableError  — LLM service outage / circuit open
    FatalPipelineError      — abort review with clear code
      └─ MCPUnreachableError   — document-mcp connection failure / circuit open
"""

from __future__ import annotations


class RecoverableError(Exception):
    """Retry or degrade — the pipeline can continue with reduced quality."""


class FatalPipelineError(Exception):
    """Abort review with a clear error code."""


class MCPUnreachableError(FatalPipelineError):
    """Document-mcp is unreachable (connection refused, circuit open, etc.)."""


class LLMUnavailableError(RecoverableError):
    """LLM service is down or circuit breaker is open."""
