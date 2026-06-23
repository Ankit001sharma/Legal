"""Phase 3 — Statement classification."""

from __future__ import annotations

from deep_research_from_scratch.validation.models import StatementClass, ValidatedClaim


def classify_statement(claim: ValidatedClaim, primary_source_count: int = 0) -> StatementClass:
    """Map support level and source quality to statement classification."""
    level = claim.support_level
    src_count = claim.source_count

    if level == "direct" and primary_source_count >= 1:
        return "FACT"
    if level == "direct":
        return "STRONG_INFERENCE"
    if level == "indirect" and src_count >= 2:
        return "STRONG_INFERENCE"
    if level == "indirect":
        return "INFERENCE"
    if level == "weak":
        return "HYPOTHESIS"
    return "UNCERTAIN"


def classify_all_claims(
    claims: list[ValidatedClaim],
    primary_source_ids: set[int] | None = None,
) -> list[ValidatedClaim]:
    """Apply classification labels to all validated claims."""
    primary_ids = primary_source_ids or set()
    updated: list[ValidatedClaim] = []
    for claim in claims:
        primary_count = sum(1 for sid in claim.supporting_source_ids if sid in primary_ids)
        classification = classify_statement(claim, primary_count)
        data = claim.model_dump()
        data["classification"] = classification
        updated.append(ValidatedClaim(**data))
    return updated
