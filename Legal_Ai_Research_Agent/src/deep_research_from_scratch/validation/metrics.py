"""Aggregate quality metrics for a research run."""

from __future__ import annotations

from deep_research_from_scratch.validation.models import ResearchQualityMetrics


def record_metrics_snapshot(metrics: ResearchQualityMetrics) -> dict:
    """Serialize metrics for audit logging."""
    return metrics.model_dump()
