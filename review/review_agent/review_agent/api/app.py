"""DEPRECATED: Public API removed — use legal_ai_platform POST /query only.

This module remains for local integration tests that invoke the graph directly.
Do not expose uvicorn review_agent.api.app in production.
"""

from __future__ import annotations

raise ImportError(
    "review_agent.api.app is deprecated. "
    "Use legal_ai_platform.gateway.app POST /query with task_type='review' instead."
)
