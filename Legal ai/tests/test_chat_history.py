"""Tests for local chat history persistence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chat_history as history


def test_save_list_load_and_delete_chat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)

    chat_id = history.new_chat_id()
    messages = [
        {"role": "user", "content": "What is Article 21?"},
        {"role": "assistant", "content": "Article 21 protects life and liberty.", "success": True},
    ]
    history.save_chat(
        chat_id=chat_id,
        messages=messages,
        thread_id="thread-1",
        research_mode="Normal Research",
    )

    summaries = history.list_chats()
    assert len(summaries) == 1
    assert summaries[0]["title"] == "What is Article 21?"

    loaded = history.load_chat(chat_id)
    assert loaded is not None
    assert loaded["thread_id"] == "thread-1"
    assert len(loaded["messages"]) == 2

    history.delete_chat(chat_id)
    assert history.load_chat(chat_id) is None
    assert history.list_chats() == []


def test_group_chats_by_period(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    now = history._utc_now()
    today = history._iso(now)
    older = history._iso(now.replace(year=now.year - 1))

    history._write_index(
        [
            {"id": "a", "title": "Today chat", "updated_at": today},
            {"id": "b", "title": "Old chat", "updated_at": older},
        ],
        tenant_id=None,
        user_id="_unknown",
    )

    grouped = dict(history.group_chats_by_period(history.list_chats()))
    assert "Today" in grouped
    assert "Older" in grouped
