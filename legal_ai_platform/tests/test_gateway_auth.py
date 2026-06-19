"""Gateway auth and session access integration tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from legal_ai_platform.auth.passwords import hash_password
from legal_ai_platform.config import get_settings
from legal_ai_platform.db.models import Tenant, User
from legal_ai_platform.db.session import get_session_factory, init_db
from legal_ai_platform.gateway.app import app


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    get_settings.cache_clear()
    init_db(get_settings().database_url)

    factory = get_session_factory(get_settings().database_url)
    db = factory()
    tenant = Tenant(id="acme", name="Acme Corp")
    user_a = User(
        id=str(uuid.uuid4()),
        email="alice@acme.com",
        password_hash=hash_password("password123"),
        role="tenant_user",
        tenant_id="acme",
    )
    user_b = User(
        id=str(uuid.uuid4()),
        email="bob@acme.com",
        password_hash=hash_password("password123"),
        role="tenant_user",
        tenant_id="acme",
    )
    db.add(tenant)
    db.add(user_a)
    db.add(user_b)
    db.commit()
    user_a_id = user_a.id
    user_b_id = user_b.id
    db.close()

    with TestClient(app) as client:
        login_a = client.post(
            "/auth/login",
            json={"email": "alice@acme.com", "password": "password123"},
        )
        login_b = client.post(
            "/auth/login",
            json={"email": "bob@acme.com", "password": "password123"},
        )
        assert login_a.status_code == 200
        assert login_b.status_code == 200
        yield client, login_a.json()["access_token"], login_b.json()["access_token"], user_a_id, user_b_id

    get_settings.cache_clear()


def test_query_requires_auth_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.post("/query", json={"query": "test"})
        assert response.status_code == 401
    get_settings.cache_clear()


def test_session_hijack_blocked(auth_client):
    client, token_a, token_b, _, _user_b_id = auth_client
    session_id = str(uuid.uuid4())

    first = client.post(
        "/query",
        json={"query": "hello", "session_id": session_id, "task_type": ["research"]},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert first.status_code in {200, 404, 500}

    hijack = client.post(
        "/query",
        json={"query": "follow up", "session_id": session_id, "task_type": ["research"]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert hijack.status_code == 403
