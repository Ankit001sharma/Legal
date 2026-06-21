"""Tests for bootstrap_env review settings loading (Phase 22 P5)."""

from __future__ import annotations

import bootstrap_env


def test_should_load_review_env_key_discovery():
    assert bootstrap_env._should_load_review_env_key("DISCOVERY_MAX_POLICIES")


def test_should_load_review_env_key_llm():
    assert bootstrap_env._should_load_review_env_key("LLM_API_KEY")


def test_should_load_review_env_key_mistral_alias():
    assert bootstrap_env._should_load_review_env_key("MISTRAL_API_KEY")


def test_should_not_load_unrelated_key():
    assert not bootstrap_env._should_load_review_env_key("DATABASE_URL")
