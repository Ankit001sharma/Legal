"""Graph node timing (Phase 31)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from review_agent.observability.context import set_current_node
from review_agent.observability.metrics import record_node_duration


def merge_node_timing(
    state: dict[str, Any],
    out: dict[str, Any],
    node_name: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    stats = dict(state.get("compliance_stats") or {})
    stats.update(out.get("compliance_stats") or {})
    timings = dict(stats.get("node_timings_ms") or {})
    timings[node_name] = elapsed_ms
    stats["node_timings_ms"] = timings
    merged = dict(out)
    merged["compliance_stats"] = stats
    return merged


def wrap_node(node_name: str, fn: Callable[..., Awaitable[dict[str, Any] | None]]):
    async def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        set_current_node(node_name)
        start = time.perf_counter()
        try:
            out = await fn(state, *args, **kwargs)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record_node_duration(node_name, elapsed_ms / 1000.0)
            return merge_node_timing(state, out or {}, node_name, elapsed_ms)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record_node_duration(node_name, elapsed_ms / 1000.0)
            raise

    return wrapped
