"""Policy category taxonomy for metadata-filtered retrieval."""

from __future__ import annotations

# Standard policy families (extend via ingest metadata.categories).
STANDARD_POLICY_CATEGORIES: frozenset[str] = frozenset(
    {
        "security",
        "vendor_security",
        "privacy",
        "data_retention",
        "confidentiality",
        "indemnity",
        "liability",
        "termination",
        "ip",
        "employment",
        "hr",
        "procurement",
        "ai_usage",
        "governing_law",
        "payment",
        "sla",
        "insurance",
        "minerals",
        "human_rights",
        "labor",
        "compliance",
        "environment",
        "sustainability",
        "general",
    }
)


# Java sync / playbook labels that differ from taxonomy canonical names.
_CATEGORY_ALIASES: dict[str, str] = {
    "indemnification": "indemnity",
    "indemnify": "indemnity",
    "hold_harmless": "indemnity",
    "data_protection": "privacy",
    "limitation_of_liability": "liability",
    "limitation_of_liability_cap": "liability",
    "confidential_information": "confidentiality",
    "intellectual_property": "ip",
    "governing_law_and_jurisdiction": "governing_law",
    "esg": "environment",
    "responsible_minerals": "minerals",
    "conflict_minerals": "minerals",
    "forced_labor": "human_rights",
    "modern_slavery": "human_rights",
    "ghg": "environment",
    "climate": "environment",
    "code_of_conduct": "compliance",
}


def _canonical_category(key: str) -> str:
    return _CATEGORY_ALIASES.get(key, key)


def category_aliases() -> dict[str, str]:
    """Alias map for UI display and ingest hints (Java sync labels → canonical)."""
    return dict(_CATEGORY_ALIASES)


def normalize_categories(raw: list[str] | None) -> list[str]:
    """Lowercase, alias, dedupe, drop empty category tags."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        key = (item or "").strip().lower().replace(" ", "_")
        if not key:
            continue
        key = _canonical_category(key)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def taxonomy_prompt_labels() -> str:
    """Comma-separated allowed category labels for LLM prompts (excludes general)."""
    return ", ".join(sorted(STANDARD_POLICY_CATEGORIES - {"general"}))
