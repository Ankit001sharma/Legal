"""Cross-section context for classification and compare (Phase 22 P8)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from document_core.schemas.chunk import IndexedChunk
from review_agent.config import ReviewSettings, get_settings

_SURVIVAL_RANGE = re.compile(
    r"sections?\s+(\d+)\s*(?:through|to|-|–)\s*(\d+)\s+survive",
    re.IGNORECASE,
)
_EXPLICIT_REF = re.compile(r"section\s+(\d+)", re.IGNORECASE)
_TERM_TITLE = re.compile(r"\b(term|termination|survival)\b", re.IGNORECASE)
_SUBSTANTIVE_TITLE_HINT = re.compile(
    r"confidential|protection|indemn|liabil",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RelatedSectionBundle:
    primary_section_id: str
    related: list[tuple[str, str, str]]
    resolution_reason: str


def _excerpt(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 3)] + "..."


def _sections_by_id(sections: list[IndexedChunk]) -> dict[str, IndexedChunk]:
    return {str(s.section_id): s for s in sections}


def _range_ids(start: int, end: int) -> list[str]:
    lo, hi = min(start, end), max(start, end)
    return [str(n) for n in range(lo, hi + 1)]


def resolve_related_sections(
    section: IndexedChunk,
    all_sections: list[IndexedChunk],
    *,
    settings: ReviewSettings | None = None,
    excerpt_chars: int = 1500,
) -> RelatedSectionBundle:
    """Build related section excerpts for survival / explicit cross-refs."""
    cfg = settings or get_settings()
    if not cfg.section_cross_ref_enabled:
        return RelatedSectionBundle(
            primary_section_id=section.section_id,
            related=[],
            resolution_reason="",
        )

    by_id = _sections_by_id(all_sections)
    text = section.text or ""
    title = (section.title or "").strip()
    related_ids: list[str] = []
    reason_parts: list[str] = []

    survival = _SURVIVAL_RANGE.search(text)
    if survival:
        start, end = int(survival.group(1)), int(survival.group(2))
        range_ids = _range_ids(start, end)
        related_ids.extend(range_ids)
        reason_parts.append(f"survival_{start}_{end}")

    explicit_refs = _EXPLICIT_REF.findall(text)
    for ref in explicit_refs[:3]:
        if ref not in related_ids:
            related_ids.append(ref)
            reason_parts.append(f"explicit_ref_{ref}")

    if _TERM_TITLE.search(title) and survival:
        for sid, candidate in by_id.items():
            if sid == section.section_id or sid in related_ids:
                continue
            cand_title = (candidate.title or "").strip()
            if _SUBSTANTIVE_TITLE_HINT.search(cand_title):
                related_ids.append(sid)
                if len([r for r in related_ids if r != section.section_id]) >= 4:
                    break

    related_ids = [sid for sid in related_ids if sid != section.section_id and sid in by_id]

    related: list[tuple[str, str, str]] = []
    for sid in related_ids:
        ref_section = by_id[sid]
        related.append(
            (
                sid,
                (ref_section.title or sid).strip(),
                _excerpt(ref_section.text or "", excerpt_chars),
            )
        )

    return RelatedSectionBundle(
        primary_section_id=section.section_id,
        related=related,
        resolution_reason=",".join(reason_parts),
    )


def build_classification_context(bundle: RelatedSectionBundle | None) -> str:
    """Compact text for lexical classification scans."""
    if bundle is None or not bundle.related:
        return ""
    parts: list[str] = []
    for sid, ref_title, excerpt in bundle.related[:4]:
        parts.append(f"§{sid} {ref_title}: {excerpt[:500]}")
    return " ".join(parts)


def format_compare_related_block(
    bundles: dict[str, RelatedSectionBundle],
    *,
    max_total_chars: int = 3000,
) -> str:
    """Markdown block appended to section compare user prompt."""
    if not bundles:
        return ""
    lines = [
        "### Related contract sections (incorporated by reference / survival)",
        "Use these excerpts when evaluating survival, term, and cross-referenced obligations.",
    ]
    used = 0
    for bundle in bundles.values():
        if not bundle.related:
            continue
        for sid, ref_title, excerpt in bundle.related:
            line = f'§{sid} {ref_title}: "{excerpt}"'
            if used + len(line) > max_total_chars:
                return "\n".join(lines)
            lines.append(line)
            used += len(line)
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def resolve_all_related_sections(
    sections: list[IndexedChunk],
    *,
    settings: ReviewSettings | None = None,
) -> dict[str, RelatedSectionBundle]:
    cfg = settings or get_settings()
    return {
        section.section_id: resolve_related_sections(section, sections, settings=cfg)
        for section in sections
    }
