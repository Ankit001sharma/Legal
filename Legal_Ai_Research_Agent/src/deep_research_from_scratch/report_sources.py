"""Post-processing helpers to ensure citation quality in delivered memoranda."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    citation_label,
    filter_citable_sources,
)

_TIMELINE_MARKERS = (
    ("incident", ("incident", "occurrence", "accident", "offence", "offense", "crash")),
    ("fir", ("fir", "first information report", "complaint registered")),
    ("arrest", ("arrest", "taken into custody", "custody")),
    ("bail", ("bail", "anticipatory bail", "interim bail")),
    ("chargesheet", ("chargesheet", "charge sheet", "charge-sheet", "final report")),
    ("trial", ("trial", "sessions court", "framing of charge", "charge framed")),
    ("appeal", ("appeal", "revision", "writ petition", "special leave")),
)


def build_source_index_map(sources: list[RetrievedSource]) -> dict[int, RetrievedSource]:
    citable = filter_citable_sources(sources)
    return {index: src for index, src in enumerate(citable, 1)}


_AUTHORITY_TIER_LABEL: dict[str, str] = {
    "primary": "Primary authority",
    "secondary": "Secondary source",
    "unknown": "Source",
}


def linkify_citations(report: str, sources: list[RetrievedSource]) -> str:
    """Replace inline [Label:n] tokens with HTML anchor tags that carry hover tooltips.

    Tooltips show: source title · authority tier · fetch status.
    Lines inside the ``## Table of Authorities`` block are left unchanged because
    they already carry fully-formed markdown links.
    """
    if not report:
        return report

    index_map = build_source_index_map(sources)
    if not index_map:
        return report

    def _replace(match: re.Match[str]) -> str:
        label = match.group(1)
        number = int(match.group(2))
        src = index_map.get(number)
        if src is None:
            return match.group(0)
        url = (src.url or "").strip()
        if not url.startswith("http"):
            return match.group(0)

        title = (src.title or "Source").strip()[:100]
        tier = _AUTHORITY_TIER_LABEL.get(src.authority_tier or "unknown", "Source")
        status = "Full text retrieved" if src.fetched else "Snippet only"
        # Escape quotes in tooltip so the HTML attribute stays valid
        tooltip = f"{title} · {tier} · {status}".replace('"', "&quot;")
        return (
            f'<a href="{url}" title="{tooltip}" '
            f'target="_blank" rel="noopener noreferrer">[{label}]</a>'
        )

    _CITATION_RE = re.compile(r"\[((?:[A-Za-z][A-Za-z\s]*:\s*)?(\d+))\](?!\()")

    result_lines: list[str] = []
    in_authorities = False
    for line in report.splitlines():
        stripped = line.strip()
        # Enter/exit the Table of Authorities block
        if re.match(r"^##\s+Table of Authorities", stripped, re.IGNORECASE):
            in_authorities = True
            result_lines.append(line)
            continue
        if in_authorities and re.match(r"^##\s+\S", stripped) and not re.match(
            r"^##\s+Table of Authorities", stripped, re.IGNORECASE
        ):
            in_authorities = False
        # Don't touch lines inside the authorities table
        result_lines.append(line if in_authorities else _CITATION_RE.sub(_replace, line))

    return "\n".join(result_lines)


def build_procedural_timeline_digest(
    sources: list[RetrievedSource],
    research_brief: str = "",
) -> str:
    """Surface procedural milestones from fetched excerpts for the writer."""
    fetched = [s for s in filter_citable_sources(sources) if s.fetched and s.excerpt]
    if not fetched:
        return (
            "(No procedural timeline hints — build Case Timeline from Findings; "
            "mark undocumented milestones as NOT FOUND in retrieved sources.)"
        )

    lines = [
        "## Procedural Timeline Hints — use in Case Timeline section",
        "Extract dates/events ONLY from the excerpts below. Do not invent dates.",
        "",
    ]
    for milestone, keywords in _TIMELINE_MARKERS:
        hits: list[str] = []
        for index, src in enumerate(fetched, 1):
            excerpt = (src.excerpt or "").lower()
            if any(keyword in excerpt for keyword in keywords):
                snippet = src.excerpt[:400].replace("\n", " ")
                hits.append(f"- [{index}] {src.title}: {snippet}")
        lines.append(f"### {milestone.title()}")
        if hits:
            lines.extend(hits[:3])
        else:
            lines.append("- Not documented in retrieved sources.")
        lines.append("")

    if research_brief.strip():
        lines.append(f"Brief context: {research_brief.strip()[:500]}")
    return "\n".join(lines).rstrip()


def build_case_digest(sources: list[RetrievedSource]) -> str:
    """Structured per-case digest so the writer analyzes every fetched judgment."""
    fetched = [
        s for s in filter_citable_sources(sources) if s.fetched and s.url
    ]
    if not fetched:
        return "(No case digest — no fetched primary sources. Do not invent holdings.)"

    lines = [
        "## Case Digest — analyze EACH fetched source below in Discussion",
        "Use the full URL exactly as shown. Do not truncate URLs.",
        "",
    ]
    for index, src in enumerate(fetched, 1):
        lines.append(f"### [{index}] {src.title}")
        lines.append(f"Full URL: {src.url}")
        lines.append(f"Status: FETCHED | Tier: {src.authority_tier}")
        if src.citation:
            lines.append(f"Citation: {src.citation}")
        if src.excerpt:
            lines.append(f"Key text from source:\n{src.excerpt[:1500]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def ensure_sources_section(report: str, sources: list[RetrievedSource]) -> str:
    """Ensure ### Sources lists every citable registry entry with clickable links."""
    citable = filter_citable_sources(sources)
    entries: list[str] = []
    for index, src in enumerate(citable, 1):
        url = (src.url or "").strip()
        if not url.startswith("http"):
            continue
        title = (src.title or "Source").strip()
        status = "fetched" if src.fetched else "snippet only"
        label = citation_label(src, index)
        entries.append(f"{label} [{title}]({url}) — {status}")

    if not entries:
        return report

    text = report or ""
    text = re.sub(
        r"\n## Table of Authorities\s*[\s\S]*?(?=\n## |\Z)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\n### Sources\s*[\s\S]*?(?=\n## |\Z)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.rstrip() + "\n\n### Sources\n" + "\n".join(entries)
