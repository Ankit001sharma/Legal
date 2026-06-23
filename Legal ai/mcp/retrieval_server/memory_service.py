"""Long-term legal memory tool (MEMORY.md index + linked detail files).

File-based long-term memory shared with the research agent. Both processes run
on the same host and point at the same ``DEEP_RESEARCH_MEMORY_DIR`` so the agent
reads (in its ``load_memory`` node) exactly what this service writes.

Layout mirrors the agent's original ``memory_tools`` implementation:
    <memory_dir>/auto/MEMORY.md          # one-line pointers index
    <memory_dir>/auto/<slug>.md          # one file per saved memory
"""

from __future__ import annotations

from pathlib import Path

from deep_research_from_scratch.memory_store import (
    ENTRYPOINT_NAME,
    save_memory_file,
    search_memory_files,
)

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.logging_setup import get_logger
from mcp.retrieval_server.models import (
    MemoryMatch,
    MemorySaveResponse,
    MemorySearchResponse,
)

logger = get_logger(__name__)


class MemoryService:
    """Save and search durable long-term legal memories on disk."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _auto_dir(self) -> Path:
        """Directory holding MEMORY.md and linked long-term memory files."""
        path = Path(self._settings.memory_dir).resolve() / "auto"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(
        self, title: str, content: str, hook: str, request_id: str = "-"
    ) -> MemorySaveResponse:
        """Save a durable memory to its own file and index it in MEMORY.md."""
        memory_dir = self._auto_dir()
        filename = save_memory_file(
            memory_dir,
            title,
            content,
            hook or title,
            use_global_lock=True,
        )

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
        matches = search_memory_files(memory_dir, query)
        results = [MemoryMatch(name=name, content=content) for name, content in matches]

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
