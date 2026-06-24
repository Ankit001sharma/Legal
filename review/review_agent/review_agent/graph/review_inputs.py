"""Validate review entry inputs before graph execution."""

from __future__ import annotations

from uuid import UUID


def has_inline_policy_texts(policy_texts: list[dict] | None) -> bool:
    for policy in policy_texts or []:
        if (policy.get("text") or "").strip():
            return True
    return False


def validate_review_inputs(
    *,
    contract_text: str,
    contract_document_id: str | None,
    require_contract_document_id: bool = False,
    policy_texts: list[dict] | None = None,
    reject_inline_policies: bool = False,
) -> tuple[str | None, list[str]]:
    """Return normalized document_id and startup warnings."""
    warnings: list[str] = []
    doc_id_raw = (contract_document_id or "").strip()
    text = (contract_text or "").strip()

    if require_contract_document_id and not doc_id_raw:
        raise ValueError(
            "contract_document_id is required when REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true"
        )

    if reject_inline_policies and has_inline_policy_texts(policy_texts):
        raise ValueError(
            "Inline policy text is not allowed when REVIEW_REJECT_INLINE_POLICIES=true; "
            "sync policies to document-mcp and use policy_document_ids or policy_refs"
        )

    if not doc_id_raw and not text:
        raise ValueError("contract_text or contract_document_id is required")

    parsed_id: str | None = None
    if doc_id_raw:
        try:
            parsed_id = str(UUID(doc_id_raw))
        except ValueError as exc:
            raise ValueError(f"invalid contract_document_id: {doc_id_raw}") from exc
        if text:
            warnings.append("contract_text ignored when contract_document_id is set")

    return parsed_id, warnings
