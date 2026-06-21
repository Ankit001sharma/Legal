"""P0-2 regression: ReviewSettings exposes fields used by contract routing LLM."""

from __future__ import annotations

from review_agent.config import ReviewSettings, get_settings


def test_review_plan_llm_max_tokens_default() -> None:
    settings = ReviewSettings()
    assert settings.review_plan_llm_max_tokens == 1024


def test_get_settings_exposes_review_plan_llm_max_tokens() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert hasattr(settings, "review_plan_llm_max_tokens")
    assert settings.review_plan_llm_max_tokens > 0


def test_section_classify_mode_default() -> None:
    settings = ReviewSettings()
    assert settings.section_classify_mode == "lexical_first"
