"""Local persistence for Streamlit chat sessions."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HISTORY_DIR = Path(
    os.environ.get(
        "LEGAL_AI_CHAT_HISTORY_DIR",
        Path(__file__).resolve().parent / "data" / "chat_history",
    )
)
INDEX_FILE = "_index.json"
TITLE_MAX_LEN = 56


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ensure_dir() -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR


def _chat_path(chat_id: str) -> Path:
    return _ensure_dir() / f"{chat_id}.json"


def _read_index() -> list[dict[str, Any]]:
    path = _ensure_dir() / INDEX_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return data


def _write_index(entries: list[dict[str, Any]]) -> None:
    path = _ensure_dir() / INDEX_FILE
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def derive_title(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = (message.get("content") or "").strip().replace("\n", " ")
            if text:
                if len(text) <= TITLE_MAX_LEN:
                    return text
                return text[: TITLE_MAX_LEN - 1].rstrip() + "…"
    return "New chat"


def list_chats() -> list[dict[str, Any]]:
    """Return chat summaries sorted by most recently updated."""
    entries = [entry for entry in _read_index() if entry.get("id")]
    entries.sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    return entries


def load_chat(chat_id: str) -> dict[str, Any] | None:
    path = _chat_path(chat_id)
    if not path.exists():
        _remove_from_index(chat_id)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def delete_chat(chat_id: str) -> None:
    path = _chat_path(chat_id)
    if path.exists():
        path.unlink()
    _remove_from_index(chat_id)


def _remove_from_index(chat_id: str) -> None:
    entries = [entry for entry in _read_index() if entry.get("id") != chat_id]
    _write_index(entries)


def save_chat(
    *,
    chat_id: str,
    messages: list[dict[str, Any]],
    thread_id: str | None,
    research_mode: str,
    awaiting_input: bool = False,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Persist a chat session and update the sidebar index."""
    now = _utc_now()
    title = derive_title(messages)
    created = created_at or _iso(now)
    record: dict[str, Any] = {
        "id": chat_id,
        "title": title,
        "created_at": created,
        "updated_at": _iso(now),
        "thread_id": thread_id,
        "research_mode": research_mode,
        "awaiting_input": awaiting_input,
        "messages": messages,
    }
    _chat_path(chat_id).write_text(json.dumps(record, indent=2), encoding="utf-8")

    entries = [entry for entry in _read_index() if entry.get("id") != chat_id]
    entries.append(
        {
            "id": chat_id,
            "title": title,
            "created_at": created,
            "updated_at": record["updated_at"],
        }
    )
    entries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    _write_index(entries)
    return record


def new_chat_id() -> str:
    return str(uuid.uuid4())


def group_chats_by_period(chats: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group chats into ChatGPT-style sidebar sections."""
    now = _utc_now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    buckets: dict[str, list[dict[str, Any]]] = {
        "Today": [],
        "Yesterday": [],
        "Previous 7 Days": [],
        "Older": [],
    }

    for chat in chats:
        raw = chat.get("updated_at") or chat.get("created_at")
        if not raw:
            buckets["Older"].append(chat)
            continue
        try:
            chat_date = _parse_iso(raw).astimezone(timezone.utc).date()
        except ValueError:
            buckets["Older"].append(chat)
            continue

        if chat_date == today:
            buckets["Today"].append(chat)
        elif chat_date == yesterday:
            buckets["Yesterday"].append(chat)
        elif chat_date >= week_ago:
            buckets["Previous 7 Days"].append(chat)
        else:
            buckets["Older"].append(chat)

    return [(label, items) for label, items in buckets.items() if items]
