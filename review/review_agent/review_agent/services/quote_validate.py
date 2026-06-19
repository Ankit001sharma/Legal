"""Quote validation helpers for section-first LLM compare."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.schemas.compliance_llm import ComplianceLLMResult


def truncate_section(text: str, max_chars: int) -> str:
    """Truncate long sections without breaking mid-word when possible."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    if "\n\n" in cut:
        cut = cut.rsplit("\n\n", 1)[0]
    elif " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "\n\n[... section truncated for model context ...]"


def quote_is_substring(quote: str, haystack: str) -> bool:
    q = quote.strip()
    if not q:
        return False
    if q in haystack:
        return True
    normalized_q = " ".join(q.split())
    normalized_h = " ".join(haystack.split())
    return normalized_q in normalized_h


def validate_and_normalize_quotes(
    result: ComplianceLLMResult,
    *,
    contract_text: str,
    policy_text: str,
) -> ComplianceLLMResult:
    """Ensure quotes are verbatim substrings; downgrade invalid LLM output."""
    contract_ok = quote_is_substring(result.contract_quote, contract_text)
    policy_ok = quote_is_substring(result.policy_quote, policy_text)

    if result.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT):
        if not contract_ok or not policy_ok:
            return ComplianceLLMResult(
                status=ComplianceStatus.INCONCLUSIVE,
                severity=Severity.IMPORTANT,
                contract_quote=result.contract_quote if contract_ok else "",
                policy_quote=result.policy_quote if policy_ok else "",
                rationale=(
                    f"{result.rationale} "
                    "(Downgraded: model quotes were not exact substrings of the provided sections.)"
                )[:2000],
                confidence=result.confidence,
            )
    else:
        if result.contract_quote and not contract_ok:
            result = result.model_copy(update={"contract_quote": ""})
        if result.policy_quote and not policy_ok:
            result = result.model_copy(update={"policy_quote": ""})

    return result


def validate_gap_item_quotes(
    result: ComplianceLLMResult,
    *,
    contract_text: str,
) -> ComplianceLLMResult:
    """Validate contract quotes for gap LLM output (no policy text)."""
    contract_ok = quote_is_substring(result.contract_quote, contract_text)
    if result.status == ComplianceStatus.NON_COMPLIANT and not contract_ok:
        return ComplianceLLMResult(
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            contract_quote=result.contract_quote if contract_ok else "",
            policy_quote="",
            rationale=(
                f"{result.rationale} "
                "(Downgraded: contract quote was not an exact substring.)"
            )[:2000],
            confidence=result.confidence,
        )
    if result.contract_quote and not contract_ok:
        result = result.model_copy(update={"contract_quote": ""})
    return result
