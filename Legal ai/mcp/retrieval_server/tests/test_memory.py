"""Tests for the long-term memory endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcp.retrieval_server.config import get_settings
from mcp.retrieval_server.main import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DEEP_RESEARCH_MEMORY_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_memory_save_indexes_file(client: TestClient, tmp_path) -> None:
    response = client.post(
        "/tools/memory/save",
        json={
            "title": "Client Objectives",
            "content": "Client wishes to void their restrictive non-compete.",
            "hook": "Restrictive covenant issues.",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "client_objectives.md"
    assert data["indexed"] is True

    auto_dir = tmp_path / "auto"
    assert (auto_dir / "client_objectives.md").exists()
    assert "Restrictive covenant issues." in (auto_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_memory_search_finds_saved(client: TestClient) -> None:
    client.post(
        "/tools/memory/save",
        json={
            "title": "Limitation Period",
            "content": "Breach of contract limitation is 3 years in India.",
            "hook": "Limitation.",
        },
    )

    response = client.post("/tools/memory/search", json={"query": "limitation"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_results"] == 1
    assert data["results"][0]["name"] == "limitation_period.md"
    assert "3 years" in data["results"][0]["content"]


def test_memory_search_empty_when_no_match(client: TestClient) -> None:
    response = client.post("/tools/memory/search", json={"query": "nonexistent"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_results"] == 0
    assert data["results"] == []
