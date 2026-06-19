"""Post-retrieval enrichment: re-fetch snippets and targeted supplemental searches."""

from __future__ import annotations

import re

from deep_research_from_scratch.config import config
from deep_research_from_scratch.retrieval_bridge import run_fetch, run_search
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    count_fetches,
    is_paywall_url,
    merge_retrieved_sources,
    normalize_url,
)

_BNS_CASE_QUERIES = (
    'BNS 304 culpable homicide India court 2024 2025',
    'site:indiankanoon.org "BNS" "304" culpable homicide 2024 2025',
    'site:indiankanoon.org "BNS" section 103 murder judgment 2024 2025',
    'site:indiankanoon.org BNSS BNS supreme court 2024 2025',
)

_CHARGESHEET_QUERIES = (
    "site:indiankanoon.org chargesheet filing procedure BNSS CrPC India",
    "site:indiankanoon.org charge sheet police report final report CrPC 173",
    "site:indiacode.nic.in Bharatiya Nagarik Suraksha Sanhita chargesheet",
)

_PROCEDURAL_KEYWORDS = (
    "fir",
    "arrest",
    "bail",
    "chargesheet",
    "charge sheet",
    "trial",
    "appeal",
    "procedural",
    "investigation",
    "custody",
    "murder",
    "culpable homicide",
    "accident",
    "crash",
)


def _topic_text(research_brief: str, user_query: str = "") -> str:
    return re.sub(r"\s+", " ", (user_query or research_brief or "").strip())[:280]


def _is_criminal_or_procedural(topic: str) -> bool:
    lower = topic.lower()
    return any(word in lower for word in _PROCEDURAL_KEYWORDS)


def _supplemental_queries(research_brief: str, user_query: str = "") -> list[str]:
    topic = _topic_text(research_brief, user_query)
    if not topic:
        return list(_BNS_CASE_QUERIES[:2]) + list(_CHARGESHEET_QUERIES[:1])

    queries: list[str] = []
    if _is_criminal_or_procedural(topic):
        queries.extend(_BNS_CASE_QUERIES)
        queries.extend(_CHARGESHEET_QUERIES)
        queries.extend([
            f"site:indiankanoon.org {topic} FIR",
            f"site:indiankanoon.org {topic} bail order",
            f"site:indiankanoon.org {topic} chargesheet",
            f"site:indiankanoon.org {topic} arrest custody",
            f"site:indiankanoon.org {topic} trial court order",
            f"site:indiankanoon.org {topic} appeal high court",
        ])
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        if query not in seen:
            seen.add(query)
            ordered.append(query)
    return ordered


def refetch_snippet_sources(
    sources: list[RetrievedSource],
    *,
    max_refetches: int | None = None,
) -> tuple[list[RetrievedSource], str]:
    """Attempt to fetch full text for every non-paywall snippet-only source."""
    limit = max_refetches if max_refetches is not None else config.DEEP_SNIPPET_REFETCH_MAX
    merged = list(sources)
    blocks: list[str] = []
    refetched = 0

    pending = [
        src
        for src in sources
        if not src.fetched
        and not src.access_denied
        and (src.url or "").startswith("http")
        and not is_paywall_url(src.url)
    ]

    for src in pending:
        if refetched >= limit:
            break
        try:
            fetch_text, fetched = run_fetch(src.url)
            blocks.append(fetch_text)
            if fetched:
                merged = merge_retrieved_sources(merged, [fetched])
                if fetched.fetched:
                    refetched += 1
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"Refetch failed for {src.url}: {exc}")

    summary = (
        f"Snippet refetch pass: {refetched} source(s) upgraded to full text "
        f"({count_fetches(merged)[0]} total fetched)."
    )
    detail = "\n\n".join(blocks[:5])
    note = summary if not detail else f"{summary}\n\n{detail}"
    return merged, note


def supplement_targeted_sources(
    sources: list[RetrievedSource],
    *,
    research_brief: str,
    user_query: str = "",
    max_queries: int | None = None,
    max_fetches: int | None = None,
) -> tuple[list[RetrievedSource], str]:
    """Run targeted BNS/BNSS and chargesheet searches, then fetch primary hits."""
    if not config.ENABLE_TARGETED_SOURCE_SUPPLEMENT:
        return sources, ""

    queries = _supplemental_queries(research_brief, user_query)[
        : max_queries or config.DEEP_SUPPLEMENT_MAX_QUERIES
    ]
    if not queries:
        return sources, ""

    merged = list(sources)
    blocks: list[str] = []
    fetch_budget = max_fetches or config.DEEP_SUPPLEMENT_MAX_FETCHES
    fetched = 0
    seen_urls = {normalize_url(s.url) for s in merged if s.url}

    for query in queries:
        try:
            text, hits = run_search(query, config.DEEP_BOOTSTRAP_RESULTS_PER_QUERY)
            blocks.append(f"--- Supplemental query: {query} ---\n{text}")
            merged = merge_retrieved_sources(merged, hits)
            for src in hits:
                if fetched >= fetch_budget:
                    break
                key = normalize_url(src.url)
                if not key or key in seen_urls:
                    continue
                if src.fetched or is_paywall_url(src.url):
                    seen_urls.add(key)
                    continue
                if not (src.url or "").startswith("http"):
                    continue
                seen_urls.add(key)
                try:
                    fetch_text, fetched_src = run_fetch(src.url)
                    blocks.append(fetch_text)
                    if fetched_src:
                        merged = merge_retrieved_sources(merged, [fetched_src])
                        if fetched_src.fetched:
                            fetched += 1
                except Exception as exc:  # noqa: BLE001
                    blocks.append(f"Supplemental fetch failed for {src.url}: {exc}")
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"Supplemental search failed ({query}): {exc}")

    note = (
        f"Targeted supplement pass ({len(queries)} queries, {fetched} new fetches)."
    )
    detail = "\n\n".join(blocks[:6])
    return merged, f"{note}\n\n{detail}" if detail else note


def enrich_retrieved_sources(
    sources: list[RetrievedSource],
    *,
    research_brief: str = "",
    user_query: str = "",
) -> tuple[list[RetrievedSource], str]:
    """Re-fetch snippets, then run targeted supplemental searches."""
    notes: list[str] = []
    merged = list(sources)

    if config.ENABLE_SNIPPET_REFETCH:
        merged, refetch_note = refetch_snippet_sources(merged)
        if refetch_note:
            notes.append(refetch_note)

    merged, supplement_note = supplement_targeted_sources(
        merged,
        research_brief=research_brief,
        user_query=user_query,
    )
    if supplement_note:
        notes.append(supplement_note)

    return merged, "\n\n".join(notes).strip()
