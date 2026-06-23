"""On-disk long-term memory store (MEMORY.md index + detail files).

Shared by the research agent and the Legal ai retrieval MCP memory service.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

ENTRYPOINT_NAME = "MEMORY.md"

_file_locks: dict[str, threading.Lock] = {}
_file_locks_lock = threading.Lock()
_global_write_lock = threading.Lock()


def slugify(title: str) -> str:
    """Turn a memory title into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "memory"


def _get_file_lock(path: str) -> threading.Lock:
    with _file_locks_lock:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


def split_query_terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", (query or "").lower()) if t]


def append_index_pointer(
    memory_dir: Path,
    title: str,
    filename: str,
    hook: str,
    *,
    use_global_lock: bool = False,
) -> None:
    """Add (or replace) a pointer line in MEMORY.md for a saved memory file."""
    entrypoint = memory_dir / ENTRYPOINT_NAME
    pointer = f"- [{title}]({filename}) - {hook}"
    lock = _global_write_lock if use_global_lock else _get_file_lock(str(entrypoint))

    with lock:
        existing = entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else ""
        lines = [ln for ln in existing.split("\n") if ln.strip()]
        lines = [ln for ln in lines if f"({filename})" not in ln]
        if not lines:
            lines = ["# MEMORY", ""]
        lines.append(pointer)
        entrypoint.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_memory_file(
    memory_dir: Path,
    title: str,
    content: str,
    hook: str = "",
    *,
    use_global_lock: bool = False,
) -> str:
    """Write a memory detail file and register it in MEMORY.md. Returns filename."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(title)}.md"
    file_path = memory_dir / filename
    body = f"# {title}\n\n{content}\n"
    lock = _global_write_lock if use_global_lock else _get_file_lock(str(file_path))

    with lock:
        file_path.write_text(body, encoding="utf-8")
    append_index_pointer(
        memory_dir,
        title,
        filename,
        hook or title,
        use_global_lock=use_global_lock,
    )
    return filename


def search_memory_files(memory_dir: Path, query: str) -> list[tuple[str, str]]:
    """Search saved memory files for query terms. Returns (filename, content) pairs."""
    if not memory_dir.exists():
        return []

    terms = split_query_terms(query)
    results: list[tuple[str, str]] = []
    for md_file in sorted(memory_dir.glob("*.md")):
        if md_file.name == ENTRYPOINT_NAME:
            continue
        text = md_file.read_text(encoding="utf-8")
        haystack = text.lower()
        if not terms or any(term in haystack for term in terms):
            results.append((md_file.name, text.strip()))
    return results
