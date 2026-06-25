"""Review scope helpers for Dev UI custom review (P9)."""

from __future__ import annotations

import os
from typing import Any


def policy_document_ids_from_sync(sync: dict[str, Any]) -> list[str]:
    """Extract synced policy document IDs for review scope."""
    return [p["document_id"] for p in sync.get("policies") or [] if p.get("document_id")]


def configure_review_policy_scope(cache_clear) -> None:
    os.environ["REVIEW_POLICY_SCOPE"] = "request"
    cache_clear()
