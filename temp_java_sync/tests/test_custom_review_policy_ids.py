"""P9 policy scope helpers for custom review."""

from __future__ import annotations

import os

from review_scope import configure_review_policy_scope, policy_document_ids_from_sync


def test_policy_document_ids_from_sync_extracts_all() -> None:
    sync = {
        "policies": [
            {"document_id": "p1"},
            {"document_id": "p2"},
            {"title": "no id"},
        ]
    }
    assert policy_document_ids_from_sync(sync) == ["p1", "p2"]


def test_configure_review_policy_scope_sets_request() -> None:
    cleared = []

    def _clear() -> None:
        cleared.append(True)

    configure_review_policy_scope(_clear)
    assert os.environ["REVIEW_POLICY_SCOPE"] == "request"
    assert cleared == [True]
