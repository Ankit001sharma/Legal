"""Metadata-aware content hashing for skip/re-index decisions."""

from __future__ import annotations

import hashlib
import json

from document_core.schemas.taxonomy import normalize_categories

_HASH_METADATA_KEYS = frozenset({"categories", "policy_type", "chunk_version"})


def metadata_fingerprint(metadata: dict | None) -> dict:
    """Stable metadata subset included in content_hash."""
    if not metadata:
        return {}
    out: dict = {}
    for key in sorted(_HASH_METADATA_KEYS):
        val = metadata.get(key)
        if val is None:
            continue
        if key == "categories":
            normalized = normalize_categories(val if isinstance(val, list) else None)
            if normalized:
                out[key] = normalized
        elif key == "policy_type":
            policy_type = str(val).strip().lower()
            if policy_type:
                out[key] = policy_type
        elif key == "chunk_version":
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                pass
    return out


def content_hash(canonical_text: str, metadata: dict | None = None) -> str:
    fp = metadata_fingerprint(metadata)
    if fp:
        payload = canonical_text + "\n" + json.dumps(fp, sort_keys=True, separators=(",", ":"))
    else:
        payload = canonical_text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
