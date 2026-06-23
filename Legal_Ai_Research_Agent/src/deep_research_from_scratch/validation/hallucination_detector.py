"""Phase 7 — Hallucination detection layer."""

from __future__ import annotations

import re

from deep_research_from_scratch.validation.models import HallucinationReport, ValidatedClaim


def detect_hallucinations(
    report: str,
    claims: list[ValidatedClaim],
) -> HallucinationReport:
    """Flag claims with insufficient or mismatched evidence."""
    verified = 0
    weak = 0
    unsupported = 0
    flagged: list[str] = []

    for claim in claims:
        if claim.support_level == "direct":
            verified += 1
        elif claim.support_level in ("indirect", "weak"):
            weak += 1
        else:
            unsupported += 1
            flagged.append(claim.claim)

        # High-confidence prose with weak support
        if claim.support_level in ("weak", "unsupported"):
            # Check if claim contains specific numbers/citations implying certainty
            if re.search(r"\b\d{4}\b|\bsection\s+\d+", claim.claim, re.I):
                if claim.claim not in flagged:
                    flagged.append(claim.claim)

    total = len(claims) or 1
    potential = len(flagged)

    return HallucinationReport(
        verified_claims=verified,
        weak_claims=weak,
        unsupported_claims=unsupported,
        potential_hallucinations=potential,
        flagged_claims=flagged[:20],
    )
