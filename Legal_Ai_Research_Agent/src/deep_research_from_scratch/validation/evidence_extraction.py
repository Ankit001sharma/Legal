"""Evidence extraction for writer and claim linking."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import RetrievedSource
from deep_research_from_scratch.validation.models import EvidenceSnippet


def _split_excerpt(text: str, max_len: int = 400) -> list[str]:
    """Split excerpt into sentence-sized chunks."""
    text = (text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) <= max_len:
            current = f"{current} {sent}".strip()
        else:
            if current:
                chunks.append(current)
            current = sent[:max_len]
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]


def extract_evidence(sources: list[RetrievedSource]) -> list[EvidenceSnippet]:
    """Build atomic evidence snippets from validated sources."""
    snippets: list[EvidenceSnippet] = []
    for source in sources:
        idx = source.source_index or 0
        chunks = _split_excerpt(source.excerpt)
        if not chunks and source.title:
            chunks = [source.title]
        for i, chunk in enumerate(chunks):
            snippets.append(
                EvidenceSnippet(
                    snippet_id=f"E{idx}-{i + 1}",
                    source_index=idx,
                    text=chunk.strip(),
                    url=source.url,
                )
            )
    return snippets


def format_evidence_pack(snippets: list[EvidenceSnippet]) -> str:
    """Format evidence pack for the writer prompt."""
    if not snippets:
        return "(No evidence snippets extracted.)"
    lines = []
    for snip in snippets:
        lines.append(f"[{snip.snippet_id}] (source [{snip.source_index}]) {snip.text}")
    return "\n".join(lines)
