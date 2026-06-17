"""Optional cleanup of legacy research JSONL when platform deletes a session."""

from __future__ import annotations

import os
from pathlib import Path


def delete_legacy_research_session_files(thread_id: str) -> list[str]:
    """Remove research-package per-thread files if they exist (best-effort).

    Returns list of deleted file paths (as strings).
    """
    root = Path(os.environ.get("DEEP_RESEARCH_MEMORY_DIR", "memory")).resolve()
    candidates = [
        root / "sessions" / f"{thread_id}.jsonl",
        root / "sessions" / f"{thread_id}.summary.json",
        root / "sessions" / f"{thread_id}.verification.jsonl",
    ]
    deleted: list[str] = []
    for path in candidates:
        if path.is_file():
            path.unlink()
            deleted.append(str(path))
    return deleted
