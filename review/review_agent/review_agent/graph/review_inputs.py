"""Validate review entry inputs before graph execution."""

from __future__ import annotations

from uuid import UUID


def validate_review_inputs(
    *,
    contract_document_id: str | None,
    contract_text: str | None = None,
    policy_document_ids: list[str] | None = None,
    policy_scope: str = "indexed",
) -> tuple[str | None, list[str], list[str]]:
    """Return normalized contract id (optional), policy ids, and startup warnings."""
    warnings: list[str] = []
    text = (contract_text or "").strip()
    doc_id_raw = (contract_document_id or "").strip()

    if not doc_id_raw and not text:
        raise ValueError("contract_text or contract_document_id is required")

    parsed_id: str | None = None
    if doc_id_raw:
        try:
            parsed_id = str(UUID(doc_id_raw))
        except ValueError as exc:
            raise ValueError(f"invalid contract_document_id: {doc_id_raw}") from exc
    elif text:
        warnings.append("contract will be ingested from contract_text before review")

    policy_ids = [str(doc_id).strip() for doc_id in (policy_document_ids or []) if str(doc_id).strip()]
    scope = (policy_scope or "indexed").strip().lower()
    if scope == "request" and not policy_ids:
        raise ValueError("policy_document_ids is required when policy_scope=request")

    return parsed_id, policy_ids, warnings
