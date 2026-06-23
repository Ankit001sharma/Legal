"""Pluggable memory retrieval backend (the seam for pgvector / Qdrant).

The agents and graph never talk to a storage engine directly -- they go through
``get_memory_backend()``. Today the default is a fast, dependency-free
file/keyword backend. Swapping in semantic vector search later (pgvector or
Qdrant) is a drop-in: implement the same ``MemoryBackend`` interface and select
it with the ``MEMORY_BACKEND`` environment variable -- no changes to nodes,
tools, or prompts.

    # .env
    MEMORY_BACKEND=file        # default (keyword search over .md + transcripts)
    # MEMORY_BACKEND=pgvector  # semantic search in Postgres + pgvector (future)
    # MEMORY_BACKEND=qdrant    # semantic search in Qdrant (future)

Why a seam instead of committing to a vector DB now: the file backend keeps the
project runnable with zero infra, while this interface guarantees we can adopt
pgvector/Qdrant for scale + semantic recall without rewrites.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Protocol

from deep_research_from_scratch.memory_namespace import get_active_namespace
from deep_research_from_scratch.memory_tools import (
    RECENCY_HALF_LIFE_HOURS,
    RECENCY_WEIGHT,
    SIMILARITY_WEIGHT,
    _keyword_similarity,
    _parse_entry_timestamp,
    _recency_score,
    get_auto_mem_path,
    load_transcript,
)
from deep_research_from_scratch.memory_store import search_memory_files, split_query_terms


@dataclass
class MemoryHit:
    """A single retrieval result from a memory backend."""

    text: str
    source: str
    score: float = 0.0


class MemoryBackend(Protocol):
    """Retrieval interface shared by every backend (file, pgvector, qdrant)."""

    def search_longterm(self, query: str, k: int = 5) -> List[MemoryHit]:
        """Recall durable cross-session facts relevant to ``query``."""
        ...

    def search_session(self, session_id: str, query: str, k: int = 5) -> List[MemoryHit]:
        """Recall older turns within a session relevant to ``query``."""
        ...


def _keyword_score(haystack: str, terms: List[str]) -> int:
    """Cheap relevance score: number of query terms present in the text."""
    low = haystack.lower()
    return sum(1 for t in terms if t in low)


class FileMemoryBackend:
    """Default backend: keyword search over MEMORY.md files and JSONL transcripts.

    Fast and dependency-free. Good enough until memory volume or paraphrase
    recall justifies a vector store, at which point ``PgVectorMemoryBackend`` /
    ``QdrantMemoryBackend`` implement this same interface.
    """

    def _terms(self, query: str) -> List[str]:
        return split_query_terms(query)

    def search_longterm(self, query: str, k: int = 5) -> List[MemoryHit]:
        terms = self._terms(query)
        hits: List[MemoryHit] = []
        auto_dir = get_auto_mem_path(get_active_namespace())
        for name, text in search_memory_files(auto_dir, query):
            score = _keyword_score(text, terms) if terms else 1
            if score > 0:
                hits.append(MemoryHit(text=text, source=name, score=float(score)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def search_session(self, session_id: str, query: str, k: int = 5) -> List[MemoryHit]:
        terms = self._terms(query)
        if not terms:
            return []

        transcript = load_transcript(session_id)
        # Exclude the verbatim recent window — those turns are already injected.
        exclude_recent = 7
        searchable = transcript[:-exclude_recent] if len(transcript) > exclude_recent else []
        if not searchable:
            return []

        now = datetime.now()
        hits: List[MemoryHit] = []
        for i, entry in enumerate(searchable):
            msg = entry.get("message", {})
            text = msg.get("content", "")
            if not isinstance(text, str) or not text.strip():
                continue
            similarity = _keyword_similarity(text.strip(), terms)
            if similarity <= 0:
                continue
            recency = _recency_score(
                _parse_entry_timestamp(entry), now, RECENCY_HALF_LIFE_HOURS
            )
            combined = (SIMILARITY_WEIGHT * similarity) + (RECENCY_WEIGHT * recency)
            role = msg.get("role", entry.get("type", "unknown"))
            hits.append(
                MemoryHit(
                    text=f"[{role}] {text.strip()}",
                    source=f"{session_id}:turn{i}",
                    score=combined,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


_BACKEND_SINGLETON: MemoryBackend | None = None


def get_memory_backend() -> MemoryBackend:
    """Return the configured memory backend (singleton).

    Selected via ``MEMORY_BACKEND`` (default ``"file"``). ``pgvector`` and
    ``qdrant`` are reserved for the semantic upgrade and raise a clear error
    until implemented, so the drop-in point is explicit.
    """
    global _BACKEND_SINGLETON
    if _BACKEND_SINGLETON is not None:
        return _BACKEND_SINGLETON

    choice = (os.environ.get("MEMORY_BACKEND") or "file").strip().lower()
    if choice == "file":
        _BACKEND_SINGLETON = FileMemoryBackend()
    elif choice in ("pgvector", "qdrant"):
        raise NotImplementedError(
            f"MEMORY_BACKEND='{choice}' is not implemented yet. Implement a "
            f"backend exposing search_longterm()/search_session() (use "
            f"model_config.get_embeddings() for vectors) and register it here."
        )
    else:
        raise ValueError(f"Unknown MEMORY_BACKEND='{choice}'. Use 'file', 'pgvector', or 'qdrant'.")

    return _BACKEND_SINGLETON


def format_hits(hits: List[MemoryHit], empty: str = "No relevant memories found.") -> str:
    """Render hits as a readable block for prompt injection."""
    if not hits:
        return empty
    return "\n\n".join(f"--- {h.source} ---\n{h.text}" for h in hits)
