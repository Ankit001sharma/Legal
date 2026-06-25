"""Tests for platform session bridge in research package."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from deep_research_from_scratch.memory_tools import get_transcript_path, record_transcript
from deep_research_from_scratch.platform_session_bridge import (
    activate_platform_session,
    clear_platform_session,
    format_platform_session_context,
    platform_owns_session,
)


def test_format_platform_session_context():
    block = {
        "summary": "user asked about liability",
        "transcript_recent": [
            {"role": "user", "content": "Review contract"},
            {"role": "assistant", "content": "Found issues", "agent": "review"},
        ],
        "matter": {"last_agent": "review", "last_task_type": "review"},
    }
    text = format_platform_session_context(block)
    assert "Rolling summary" in text
    assert "[review]" in text
    assert "Review contract" in text


def test_record_transcript_skipped_when_platform_owns(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEP_RESEARCH_MEMORY_DIR", str(tmp_path))
    activate_platform_session({"platform_owns_session": True})
    try:
        assert platform_owns_session() is True
        record_transcript("sess-1", "user", "hello")
        assert not get_transcript_path("sess-1").exists()
    finally:
        clear_platform_session()


def test_record_transcript_writes_when_standalone(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEP_RESEARCH_MEMORY_DIR", str(tmp_path))
    clear_platform_session()
    record_transcript("sess-2", "user", "hello standalone")
    assert get_transcript_path("sess-2").exists()
