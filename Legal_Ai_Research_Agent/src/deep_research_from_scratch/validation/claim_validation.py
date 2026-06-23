"""Phase 2 — Evidence-based claim validation."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    build_verification_corpus,
    extract_citations,
)
from deep_research_from_scratch.validation.models import ValidatedClaim


def _extract_claims_from_report(report: str) -> list[str]:
    """Split report into atomic claim-like sentences."""
    # Strip markdown headings and boilerplate
    text = re.sub(r"^#+\s.*$", "", report or "", flags=re.MULTILINE)
    text = re.sub(r"\[.*?\]", "", text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    claims: list[str] = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 30:
            continue
        if sent.lower().startswith(("see ", "refer ", "note:", ">")):
            continue
        if any(
            skip in sent.lower()
            for skip in ("disclaimer", "not legal advice", "verification caveats")
        ):
            continue
        claims.append(sent)
    return claims[:50]


def _token_overlap(claim: str, corpus: str) -> float:
    claim_tokens = set(re.findall(r"[a-zA-Z]{4,}", claim.lower()))
    corpus_tokens = set(re.findall(r"[a-zA-Z]{4,}", corpus.lower()))
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & corpus_tokens) / len(claim_tokens)


def _find_supporting_sources(
    claim: str,
    sources: list[RetrievedSource],
) -> tuple[list[int], list[int]]:
    """Return supporting and contradicting source indices."""
    supporting: list[int] = []
    contradicting: list[int] = []
    neg_markers = ("not ", "no ", "reject", "overrule", "contrary", " unlike ")
    claim_lower = claim.lower()
    for src in sources:
        if not src.fetched and not src.excerpt:
            continue
        excerpt = f"{src.title} {src.excerpt}".lower()
        overlap = _token_overlap(claim, excerpt)
        if overlap >= 0.35:
            idx = src.source_index or 0
            if any(m in excerpt for m in neg_markers) and overlap >= 0.25:
                contradicting.append(idx)
            else:
                supporting.append(idx)
    return supporting, contradicting


def classify_support(
    claim: str,
    corpus: str,
    supporting: list[int],
    contradicting: list[int],
) -> tuple[str, int]:
    """Determine support level and confidence for a claim."""
    overlap = _token_overlap(claim, corpus)
    cite_in_corpus = any(c in corpus.upper() for c in extract_citations(claim))

    if supporting and overlap >= 0.4:
        level = "direct"
        confidence = min(95, 70 + len(supporting) * 8)
    elif supporting and overlap >= 0.25:
        level = "indirect"
        confidence = min(80, 50 + len(supporting) * 10)
    elif overlap >= 0.2 or cite_in_corpus:
        level = "weak"
        confidence = 40
    else:
        level = "unsupported"
        confidence = 10

    if contradicting and supporting:
        confidence = max(20, confidence - 15)

    return level, confidence


def validate_claims(
    report: str,
    notes: list[str],
    raw_notes: list[str],
    sources: list[RetrievedSource],
) -> list[ValidatedClaim]:
    """Validate every important claim in the report against evidence."""
    corpus = build_verification_corpus(notes, raw_notes, sources)
    claims_text = _extract_claims_from_report(report)
    validated: list[ValidatedClaim] = []

    for claim_text in claims_text:
        supporting, contradicting = _find_supporting_sources(claim_text, sources)
        support_level, confidence = classify_support(
            claim_text, corpus, supporting, contradicting
        )
        validated.append(
            ValidatedClaim(
                claim=claim_text,
                support_level=support_level,
                source_count=len(set(supporting)),
                confidence=confidence,
                supporting_source_ids=list(set(supporting)),
                contradicting_source_ids=list(set(contradicting)),
            )
        )
    return validated
