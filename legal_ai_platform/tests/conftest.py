"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from legal_ai_platform.config import get_settings


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
