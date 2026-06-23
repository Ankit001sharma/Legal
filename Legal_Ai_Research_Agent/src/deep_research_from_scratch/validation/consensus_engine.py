"""Phase 8 — Multi-source consensus engine."""

from __future__ import annotations

from deep_research_from_scratch.validation.models import ConsensusLevel, ValidatedClaim


def compute_consensus(claim: ValidatedClaim) -> ConsensusLevel:
    """Determine consensus level for a claim."""
    support = len(set(claim.supporting_source_ids))
    contradict = len(set(claim.contradicting_source_ids))

    if contradict >= 2 and support >= 2:
        return "conflicting"
    if contradict >= 1 and support >= 1:
        return "conflicting"
    if support >= 3:
        return "high"
    if support >= 2:
        return "medium"
    if support >= 1:
        return "low"
    return "low"


def apply_consensus(claims: list[ValidatedClaim]) -> list[ValidatedClaim]:
    """Assign consensus levels to all claims."""
    updated: list[ValidatedClaim] = []
    for claim in claims:
        consensus = compute_consensus(claim)
        data = claim.model_dump()
        data["consensus"] = consensus
        updated.append(ValidatedClaim(**data))
    return updated


def consensus_score(claims: list[ValidatedClaim]) -> float:
    """Aggregate consensus score 0-100."""
    if not claims:
        return 50.0
    weights = {"high": 100, "medium": 75, "low": 40, "conflicting": 30}
    total = sum(weights.get(c.consensus, 40) for c in claims)
    return round(total / len(claims), 1)
