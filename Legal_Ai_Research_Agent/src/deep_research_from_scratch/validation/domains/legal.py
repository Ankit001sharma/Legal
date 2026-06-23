"""Legal-domain validation adapter (India-focused)."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import classify_authority_tier
from deep_research_from_scratch.validation.domains.generic import GenericDomainAdapter


class LegalDomainAdapter(GenericDomainAdapter):
    """Authority scoring using legal source registry tiers."""

    _CRYPTO_KEYWORDS = (
        "crypto",
        "cryptocurrency",
        "virtual currency",
        "bitcoin",
        "blockchain",
        "virtual digital asset",
        "vda",
        "token",
        "nft",
    )

    def authority_score(self, url: str, title: str, fetched: bool) -> int:
        tier = classify_authority_tier(url)
        base = {"primary": 95, "secondary": 60, "unknown": 40}[tier]
        if not fetched:
            base = max(base - 15, 20)
        return base

    def required_landmarks(self, research_brief: str, findings: str, report: str) -> list[str]:
        combined = " ".join([research_brief, findings, report]).lower()
        if not any(kw in combined for kw in self._CRYPTO_KEYWORDS):
            return []
        missing: list[str] = []
        corpus = (findings + report).upper()
        has_iamai = "IAMAI" in corpus and "RESERVE BANK" in corpus
        if not has_iamai:
            missing.append(
                "IAMAI v RBI (Internet and Mobile Association of India v Reserve Bank of India)"
            )
        has_pmla = "PMLA" in corpus and any(
            k in corpus for k in ("CRYPTO", "VIRTUAL DIGITAL", "VDA")
        )
        if not has_pmla:
            missing.append("PMLA enforcement on virtual digital assets / cryptocurrency")
        return missing

    def extract_keywords(self, text: str) -> set[str]:
        keywords = super().extract_keywords(text)
        legal_terms = re.findall(
            r"\b(?:section|ipc|bns|crpc|bnss|pmla|contract|tort|bail|fir|"
            r"judgment|statute|act|court|supreme|high)\b",
            (text or "").lower(),
        )
        keywords.update(legal_terms)
        return keywords
