"""Tests for review input validation."""

from __future__ import annotations

import pytest
from uuid import uuid4

from review_agent.graph.review_inputs import validate_review_inputs


def test_validate_accepts_contract_text_indexed_scope() -> None:
    doc_id, policy_ids, warnings = validate_review_inputs(
        contract_document_id=None,
        contract_text="Section 1. Liability cap is $100k.",
        policy_document_ids=None,
        policy_scope="indexed",
    )
    assert doc_id is None
    assert policy_ids == []
    assert any("contract_text" in w for w in warnings)


def test_validate_requires_policy_ids_for_request_scope() -> None:
    with pytest.raises(ValueError, match="policy_document_ids"):
        validate_review_inputs(
            contract_document_id=str(uuid4()),
            policy_document_ids=[],
            policy_scope="request",
        )


def test_validate_accepts_document_id_and_policy_scope() -> None:
    cid = str(uuid4())
    pid = str(uuid4())
    doc_id, policy_ids, _warnings = validate_review_inputs(
        contract_document_id=cid,
        policy_document_ids=[pid],
        policy_scope="request",
    )
    assert doc_id == cid
    assert policy_ids == [pid]
