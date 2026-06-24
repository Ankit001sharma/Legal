"""Tests for bootstrap_env review settings loading (Phase 22 P5)."""

from __future__ import annotations

import bootstrap_env


def test_should_load_review_env_key_discovery():
    assert bootstrap_env._should_load_review_env_key("DISCOVERY_MAX_POLICIES")


def test_should_load_review_env_key_llm():
    assert bootstrap_env._should_load_review_env_key("LLM_API_KEY")


def test_should_load_review_env_key_mistral_alias():
    assert bootstrap_env._should_load_review_env_key("MISTRAL_API_KEY")


def test_setup_pythonpath_inserts_sys_path():
    import sys

    bootstrap_env.setup_pythonpath()
    legal = bootstrap_env.Path(__file__).resolve().parent.parent.parent
    doc_core = str(legal / "document_core")
    assert doc_core in sys.path
