"""Quote verification with optional LLM repair before MCP gate (P2-7)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.schemas.chunk import GetSectionRequest, GroundingCheckRequest
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.services.quote_repair_llm import repair_quote_for_section


def _grounded_in_requested_section(
    check: Any,
    section_id: str,
) -> bool:
    if not check.grounded:
        return False
    requested = (section_id or "").strip()
    if not requested:
        return True
    matched = (check.section_id or "").strip()
    if not matched:
        return True
    return matched == requested


async def _fetch_section_text(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    section_id: str,
) -> str:
    if not section_id:
        return ""
    chunk = await client.get_section(
        GetSectionRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            section_id=section_id,
        )
    )
    return (chunk.text or "").strip() if chunk else ""


async def verify_quote_with_repair(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    quote: str,
    section_id: str,
    settings: ReviewSettings,
    stats: dict[str, int],
    verify_fn: Any,
) -> tuple[str, bool, dict[str, Any]]:
    """Verify quote via MCP; on failure optionally repair from section text and re-verify."""
    candidate = (quote or "").strip()
    meta: dict[str, Any] = {}
    if not candidate:
        return "", True, meta

    check = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=candidate,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(check, section_id):
        return candidate, True, meta
    if check.grounded:
        meta["grounding_section_mismatch"] = True
        if check.section_id:
            meta["grounding_matched_section_id"] = check.section_id
        return candidate, False, meta

    if not settings.quote_repair_enabled:
        return candidate, False, meta

    source_text = await _fetch_section_text(
        client,
        tenant_id=tenant_id,
        document_id=document_id,
        section_id=section_id,
    )
    if not source_text:
        meta["grounding_repair_attempted"] = True
        stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
        return candidate, False, meta

    stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
    meta["grounding_repair_attempted"] = True
    repair = await repair_quote_for_section(
        source_text=source_text,
        candidate_quote=candidate,
        section_id=section_id,
        settings=settings,
    )
    repaired = (repair.repaired_quote or "").strip()
    if not repaired:
        return candidate, False, meta

    recheck = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=repaired,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(recheck, section_id):
        meta["quote_repair_used"] = True
        if repair.repair_notes:
            meta["quote_repair_notes"] = repair.repair_notes[:200]
        stats["quote_repair_success"] = stats.get("quote_repair_success", 0) + 1
        return repaired, True, meta

    if recheck.grounded:
        meta["grounding_section_mismatch"] = True
        if recheck.section_id:
            meta["grounding_matched_section_id"] = recheck.section_id

    return candidate, False, meta
