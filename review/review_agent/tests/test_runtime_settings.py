"""Tests for runtime settings snapshot (Phase 22 P5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from review_agent.config import ReviewSettings, build_runtime_settings_snapshot, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_discovery_max_policies_zero():
    field = ReviewSettings.model_fields["discovery_max_policies"]
    assert field.default == 0


def test_runtime_settings_snapshot_includes_compare_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPARE_POLICY_HIT_MODE", "category_aligned")
    monkeypatch.setenv("DISCOVERY_MAX_POLICIES", "0")
    get_settings.cache_clear()
    snapshot = build_runtime_settings_snapshot()
    assert snapshot["compare_policy_hit_mode"] == "category_aligned"
    assert snapshot["discovery_max_policies"] == 0


def test_runtime_settings_snapshot_redacts_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    get_settings.cache_clear()
    snapshot = build_runtime_settings_snapshot()
    assert "llm_api_key" not in snapshot
    assert "super-secret-key" not in str(snapshot.values())


def test_runtime_settings_snapshot_includes_reranker(monkeypatch: pytest.MonkeyPatch):
    core = MagicMock()
    core.reranker_enabled = True
    core.reranker_backend = "cross_encoder"
    snapshot = build_runtime_settings_snapshot(core=core)
    assert snapshot["reranker_enabled"] is True
    assert snapshot["reranker_backend"] == "cross_encoder"


def test_runtime_settings_snapshot_reranker_off_when_disabled():
    core = MagicMock()
    core.reranker_enabled = False
    core.reranker_backend = "cross_encoder"
    snapshot = build_runtime_settings_snapshot(core=core)
    assert snapshot["reranker_backend"] == "off"
