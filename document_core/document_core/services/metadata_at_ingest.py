"""Ingest-time policy category inference (document_core only — no normalization imports)."""

from __future__ import annotations

import re

from document_core.schemas.taxonomy import STANDARD_POLICY_CATEGORIES, normalize_categories

_CATEGORY_PHRASES: tuple[tuple[str, str], ...] = (
    ("code of conduct", "compliance"),
    ("code-of-conduct", "compliance"),
    ("limitation of liability", "liability"),
    ("limitation_of_liability", "liability"),
    ("confidential information", "confidentiality"),
    ("non-disclosure", "confidentiality"),
    ("non disclosure", "confidentiality"),
    ("indemnification", "indemnity"),
    ("indemnify", "indemnity"),
    ("hold harmless", "indemnity"),
    ("data protection", "privacy"),
    ("governing law", "governing_law"),
    ("intellectual property", "ip"),
    ("termination", "termination"),
    ("confidentiality", "confidentiality"),
    ("confidential", "confidentiality"),
    ("liability", "liability"),
    ("indemnity", "indemnity"),
    ("privacy", "privacy"),
    ("insurance", "insurance"),
    ("payment", "payment"),
    ("procurement", "procurement"),
    ("employment", "employment"),
    ("compliance", "compliance"),
    ("conduct", "compliance"),
    ("security", "security"),
    ("human rights", "human_rights"),
    ("sla", "sla"),
)


def _infer_categories(*, title: str, section_texts: list[str]) -> list[str]:
    haystack = " ".join([title, *section_texts[:2]])[:4000].lower()
    found: list[str] = []
    seen: set[str] = set()

    for phrase, category in _CATEGORY_PHRASES:
        if category in seen:
            continue
        if phrase in haystack:
            seen.add(category)
            found.append(category)

    token_source = title.lower()
    for token in re.split(r"[^a-z0-9]+", token_source):
        if not token or token in seen:
            continue
        for cat in normalize_categories([token]):
            if cat in STANDARD_POLICY_CATEGORIES and cat != "general" and cat not in seen:
                seen.add(cat)
                found.append(cat)

    return found or ["general"]


def infer_section_categories_keyword(*, title: str, text: str) -> list[str]:
    """Per-section keyword/phrase infer; returns 1+ categories or ['general']."""
    return _infer_categories(title=title, section_texts=[text])


def _explicit_categories(provided: list[str] | None, metadata: dict | None) -> list[str]:
    meta_raw = (metadata or {}).get("categories")
    meta_cats = normalize_categories(meta_raw if isinstance(meta_raw, list) else None)
    if meta_cats and meta_cats != ["general"]:
        return meta_cats
    norm_provided = normalize_categories(provided)
    if norm_provided and norm_provided != ["general"]:
        return norm_provided
    return []


def resolve_ingest_categories(
    *,
    title: str,
    section_texts: list[str],
    provided: list[str] | None,
    metadata: dict | None,
) -> tuple[list[str], dict[str, object]]:
    """Return resolved categories and extra metadata fields for ingest."""
    explicit = _explicit_categories(provided, metadata)
    if explicit:
        return explicit, {}

    inferred = _infer_categories(title=title, section_texts=section_texts)
    extra: dict[str, object] = {"auto_tagged": True}
    return inferred, extra
