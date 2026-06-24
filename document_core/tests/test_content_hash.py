"""Metadata-aware content hash tests."""

from __future__ import annotations

from document_core.store.content_hash import content_hash


def test_content_hash_metadata_change():
    text = "12.2 Limitation of Liability\nCap is fees paid."
    hash_a = content_hash(text, {"categories": ["liability"]})
    hash_b = content_hash(text, {"categories": ["privacy"]})
    assert hash_a != hash_b


def test_content_hash_same_metadata_matches():
    text = "12.2 Limitation of Liability\nCap is fees paid."
    meta = {"categories": ["liability"], "policy_type": "nda"}
    assert content_hash(text, meta) == content_hash(text, meta)


def test_content_hash_chunk_version_change():
    text = "12.2 Limitation of Liability\nCap is fees paid."
    assert content_hash(text, {"chunk_version": 1}) != content_hash(text, {"chunk_version": 2})
