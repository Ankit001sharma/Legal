"""Tests for cross-encoder reranker service."""

from __future__ import annotations

import builtins

import pytest

from document_core.config import DocumentCoreSettings
from document_core.embeddings import reranker_service


def test_reranker_available_false_without_sentence_transformers(monkeypatch):
    monkeypatch.setattr(
        reranker_service,
        "get_settings",
        lambda: DocumentCoreSettings(reranker_enabled=True),
    )
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            raise ImportError("sentence_transformers unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert reranker_service.reranker_available() is False


def test_score_query_passages_batches_pairs(monkeypatch):
    captured: dict[str, object] = {}

    class FakeCrossEncoder:
        def __init__(self, model_name: str) -> None:
            captured["model_name"] = model_name

        def predict(self, pairs):
            captured["pairs"] = pairs
            return [0.2, 0.8]

    monkeypatch.setattr(
        reranker_service,
        "get_settings",
        lambda: DocumentCoreSettings(
            reranker_enabled=True,
            reranker_model="test-reranker",
        ),
    )
    monkeypatch.setattr(
        reranker_service,
        "_load_cross_encoder",
        lambda model_name: FakeCrossEncoder(model_name),
    )

    scores = reranker_service.score_query_passages(
        "limitation of liability",
        ["privacy policy", "liability cap section"],
    )
    assert scores == [0.2, 0.8]
    assert captured["model_name"] == "test-reranker"
    pairs = captured["pairs"]
    assert pairs == [
        ("limitation of liability", "privacy policy"),
        ("limitation of liability", "liability cap section"),
    ]


def test_score_query_passages_empty_query_returns_none():
    assert reranker_service.score_query_passages("", ["passage"]) is None
