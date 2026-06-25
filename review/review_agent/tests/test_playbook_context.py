"""Tests for dynamic playbook hint builder (P4.1)."""

from review_agent.services.playbook_context import (
    PlaybookHints,
    build_playbook_hints_by_document,
    format_playbook_hint_block,
    hints_from_chunk_metadata,
)


def test_format_playbook_hint_block_empty():
    assert format_playbook_hint_block(None) == ""
    assert format_playbook_hint_block(PlaybookHints()) == ""


def test_format_playbook_hint_block_with_fields():
    hints = PlaybookHints(
        policy_ref="vendor-msa-liability",
        review_guidance="Cap should align with fees paid in prior 12 months.",
        preferred_position="Liability capped at 12 months fees.",
    )
    block = format_playbook_hint_block(hints)
    assert "Playbook hints" in block
    assert "vendor-msa-liability" in block
    assert "Cap should align" in block
    assert "Liability capped at 12 months fees." in block


def test_build_playbook_hints_from_indexed_policies():
    indexed = [
        {
            "document_id": "doc-1",
            "policy_ref": "privacy-standard",
            "metadata": {
                "review_guidance": "Require breach notification within 72 hours.",
            },
        }
    ]
    hints_map = build_playbook_hints_by_document(indexed)
    assert "doc-1" in hints_map
    assert hints_map["doc-1"].policy_ref == "privacy-standard"
    assert "72 hours" in (hints_map["doc-1"].review_guidance or "")


def test_hints_from_chunk_metadata():
    hints = hints_from_chunk_metadata(
        {"preferred_position": "Mutual indemnification only."}
    )
    assert hints is not None
    assert hints.preferred_position == "Mutual indemnification only."
