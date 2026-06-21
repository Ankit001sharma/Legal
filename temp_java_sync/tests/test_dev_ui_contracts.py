"""Dev UI contract regression tests (P3-9)."""

from __future__ import annotations

from review_output import build_platform_review_payload


def test_platform_review_payload_includes_query():
    payload = build_platform_review_payload(
        tenant_id="e2e-demo",
        contract_document_id="doc-123",
        contract_title="Mutual NDA (Dev UI)",
        contract_type="nda",
    )
    assert "query" in payload
    assert payload["query"].strip()
    assert payload["task_type"] == "review"
    assert payload["contract_document_id"] == "doc-123"
