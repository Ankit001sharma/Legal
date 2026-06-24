"""Tests for ReviewState reducers (Phase 30)."""

from __future__ import annotations

from review_agent.state.review_state import merge_warnings


def test_merge_warnings_dedupes() -> None:
    assert merge_warnings(["a"], ["a", "b"]) == ["a", "b"]
    assert merge_warnings(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_merge_warnings_empty_new() -> None:
    assert merge_warnings(["a"], []) == ["a"]
