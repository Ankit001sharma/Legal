"""Quote verification for dual grounding."""

from __future__ import annotations

import re

from document_core.schemas.chunk import GroundingCheckRequest, GroundingCheckResult
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", text.strip().lower())


def _match_in_text(quote_norm: str, text: str) -> bool:
    return quote_norm in normalize_text(text)


async def _verify_quote_in_section(
    request: GroundingCheckRequest,
    *,
    doc_store: DocumentStore,
    quote_norm: str,
) -> GroundingCheckResult:
    section_id = (request.section_id or "").strip()
    if not section_id:
        return GroundingCheckResult(
            grounded=False,
            quote=request.quote,
            normalized_quote=quote_norm,
            message="section_id required for strict grounding",
        )

    parent = doc_store.get_parent_by_section(
        request.tenant_id,
        request.document_id,
        section_id,
    )
    if parent is None:
        return GroundingCheckResult(
            grounded=False,
            quote=request.quote,
            normalized_quote=quote_norm,
            section_id=section_id,
            message="section not found",
        )

    if _match_in_text(quote_norm, parent.text or ""):
        return GroundingCheckResult(
            grounded=True,
            quote=request.quote,
            normalized_quote=quote_norm,
            section_id=parent.section_id,
            message="quote found in section text",
        )

    return GroundingCheckResult(
        grounded=False,
        quote=request.quote,
        normalized_quote=quote_norm,
        section_id=section_id,
        message="quote not found in section text",
    )


async def _verify_quote_document_wide(
    request: GroundingCheckRequest,
    *,
    doc_store: DocumentStore,
    quote_norm: str,
) -> GroundingCheckResult:
    haystacks: list[tuple[str, str | None]] = []

    canonical = doc_store.get_canonical_text(request.tenant_id, request.document_id)
    if canonical:
        haystacks.append((canonical, request.section_id))

    for parent in doc_store.get_parents(request.tenant_id, request.document_id):
        haystacks.append((parent.text, parent.section_id))

    seen: set[str] = set()
    for text, section_id in haystacks:
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        if _match_in_text(quote_norm, text):
            return GroundingCheckResult(
                grounded=True,
                quote=request.quote,
                normalized_quote=quote_norm,
                section_id=section_id,
                message="quote found in source text",
            )

    return GroundingCheckResult(
        grounded=False,
        quote=request.quote,
        normalized_quote=quote_norm,
        section_id=request.section_id,
        message="quote not found in source text",
    )


async def verify_quote(
    request: GroundingCheckRequest,
    *,
    store: DocumentStore | None = None,
) -> GroundingCheckResult:
    """Substring match on section text when section_id set; else document-wide."""
    doc_store = store or get_store()
    quote_norm = normalize_text(request.quote)
    if not quote_norm:
        return GroundingCheckResult(
            grounded=False,
            quote=request.quote,
            normalized_quote=quote_norm,
            message="empty quote",
        )

    if (request.section_id or "").strip():
        return await _verify_quote_in_section(
            request,
            doc_store=doc_store,
            quote_norm=quote_norm,
        )

    return await _verify_quote_document_wide(
        request,
        doc_store=doc_store,
        quote_norm=quote_norm,
    )
