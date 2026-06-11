"""Long-term legal memory tool (MEMORY.md index + linked detail files).

File-based long-term memory shared with the research agent. Both processes run
on the same host and point at the same ``DEEP_RESEARCH_MEMORY_DIR`` so the agent
reads (in its ``load_memory`` node) exactly what this service writes.

Layout mirrors the agent's original ``memory_tools`` implementation:
    <memory_dir>/auto/MEMORY.md          # one-line pointers index
    <memory_dir>/auto/<slug>.md          # one file per saved memory
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.logging_setup import get_logger
from mcp.retrieval_server.models import (
    MemoryMatch,
    MemorySaveResponse,
    MemorySearchResponse,
)

logger = get_logger(__name__)

ENTRYPOINT_NAME = "MEMORY.md"

# Serializes writes to MEMORY.md / detail files so concurrent requests cannot
# corrupt the append-only index.
_write_lock = threading.Lock()


def _slugify(title: str) -> str:
    """Turn a memory title into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "memory"


class MemoryService:
    """Save and search durable long-term legal memories on disk."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _auto_dir(self) -> Path:
        """Directory holding MEMORY.md and linked long-term memory files."""
        path = Path(self._settings.memory_dir).resolve() / "auto"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _append_index_pointer(
        self, memory_dir: Path, title: str, filename: str, hook: str
    ) -> None:
        """Add (or replace) a pointer line in MEMORY.md for a saved memory file."""
        entrypoint = memory_dir / ENTRYPOINT_NAME
        pointer = f"- [{title}]({filename}) - {hook}"

        with _write_lock:
            existing = (
                entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else ""
            )
            lines = [ln for ln in existing.split("\n") if ln.strip()]
            # Replace an existing pointer to the same file, else append.
            lines = [ln for ln in lines if f"({filename})" not in ln]
            if not lines:
                lines = ["# MEMORY", ""]
            lines.append(pointer)
            entrypoint.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def save(
        self, title: str, content: str, hook: str, request_id: str = "-"
    ) -> MemorySaveResponse:
        """Save a durable memory to its own file and index it in MEMORY.md."""
        memory_dir = self._auto_dir()
        filename = f"{_slugify(title)}.md"
        file_path = memory_dir / filename

        body = f"# {title}\n\n{content}\n"
        with _write_lock:
            file_path.write_text(body, encoding="utf-8")
        self._append_index_pointer(memory_dir, title, filename, hook or title)

        logger.info(
            "memory saved",
            request_id=request_id,
            filename=filename,
            memory_dir=str(memory_dir),
        )
        return MemorySaveResponse(
            request_id=request_id,
            filename=filename,
            indexed=True,
            message=f"Memory saved to {filename} and indexed in {ENTRYPOINT_NAME}.",
        )

    def search(self, query: str, request_id: str = "-") -> MemorySearchResponse:
        """Search saved memory files for query terms, returning matching files."""
        memory_dir = self._auto_dir()
        terms = [t for t in re.split(r"\s+", query.lower()) if t]

        results: list[MemoryMatch] = []
        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name == ENTRYPOINT_NAME:
                continue
            text = md_file.read_text(encoding="utf-8")
            haystack = text.lower()
            if not terms or any(term in haystack for term in terms):
                results.append(MemoryMatch(name=md_file.name, content=text.strip()))

        logger.info(
            "memory searched",
            request_id=request_id,
            matches=len(results),
            memory_dir=str(memory_dir),
        )
        return MemorySearchResponse(
            request_id=request_id,
            query=query,
            results=results,
            total_results=len(results),
        )
