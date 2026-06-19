"""Tests for memory namespace path resolution and RBAC policy."""

from __future__ import annotations

from pathlib import Path

from deep_research_from_scratch.memory_namespace import MemoryNamespace, resolve_memory_paths

from legal_ai_platform.auth.memory_policy import MemoryAccessPolicy
from legal_ai_platform.auth.principal import Principal, UserRole
from legal_ai_platform.auth.session_registry import SessionRegistry
from legal_ai_platform.config import get_settings
from legal_ai_platform.db.models import MemorySession


def test_platform_namespace_paths():
    ns = MemoryNamespace(tenant_id=None, user_id="admin-1")
    paths = resolve_memory_paths(ns, memory_root=Path("/mem"))
    assert paths.sessions_dir == Path("/mem/platform/users/admin-1/sessions")
    assert paths.auto_dir == Path("/mem/platform/users/admin-1/auto")


def test_tenant_namespace_paths():
    ns = MemoryNamespace(tenant_id="acme", user_id="user-1")
    paths = resolve_memory_paths(ns, memory_root=Path("/mem"))
    assert paths.sessions_dir == Path("/mem/tenants/acme/users/user-1/sessions")
    assert paths.auto_dir == Path("/mem/tenants/acme/users/user-1/auto")


def test_tenant_user_own_session():
    principal = Principal(
        user_id="u1",
        email="u1@acme.com",
        role=UserRole.TENANT_USER,
        tenant_id="acme",
    )
    record = MemorySession(session_id="s1", tenant_id="acme", user_id="u1")
    access = MemoryAccessPolicy.can_access(principal, record)
    assert access.allowed is True
    assert access.can_write is True


def test_tenant_user_cannot_access_peer_session():
    principal = Principal(
        user_id="u1",
        email="u1@acme.com",
        role=UserRole.TENANT_USER,
        tenant_id="acme",
    )
    record = MemorySession(session_id="s2", tenant_id="acme", user_id="u2")
    access = MemoryAccessPolicy.can_access(principal, record)
    assert access.allowed is False


def test_tenant_admin_read_peer_session():
    principal = Principal(
        user_id="admin",
        email="admin@acme.com",
        role=UserRole.TENANT_ADMIN,
        tenant_id="acme",
    )
    record = MemorySession(session_id="s2", tenant_id="acme", user_id="u2")
    access = MemoryAccessPolicy.can_access(principal, record)
    assert access.allowed is True
    assert access.can_write is False


def test_super_admin_cross_tenant():
    principal = Principal(
        user_id="root",
        email="root@platform.com",
        role=UserRole.SUPER_ADMIN,
        tenant_id=None,
    )
    record = MemorySession(session_id="s9", tenant_id="other", user_id="u9")
    access = MemoryAccessPolicy.can_access(principal, record)
    assert access.allowed is True
    assert access.can_write is True


def test_super_admin_tenant_override():
    principal = Principal(
        user_id="root",
        email="root@platform.com",
        role=UserRole.SUPER_ADMIN,
        tenant_id=None,
    )
    assert MemoryAccessPolicy.namespace_tenant_id(principal, "demo") == "demo"


def test_tenant_user_ignores_request_tenant_override():
    principal = Principal(
        user_id="u1",
        email="u1@acme.com",
        role=UserRole.TENANT_USER,
        tenant_id="acme",
    )
    assert MemoryAccessPolicy.namespace_tenant_id(principal, "other") == "acme"


def test_session_registry_blocks_cross_user(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'reg.db'}")
    get_settings.cache_clear()
    from legal_ai_platform.config import get_settings as gs
    from legal_ai_platform.db.session import get_session_factory, init_db

    init_db(gs().database_url)
    factory = get_session_factory(gs().database_url)
    db = factory()
    registry = SessionRegistry(db)
    owner = Principal("u1", "u1@acme.com", UserRole.TENANT_USER, "acme")
    intruder = Principal("u2", "u2@acme.com", UserRole.TENANT_USER, "acme")
    registry.register(session_id="sess-1", principal=owner, tenant_id="acme")
    _, access = registry.authorize(
        session_id="sess-1",
        principal=intruder,
        tenant_id="acme",
        allow_register=False,
    )
    assert access.allowed is False
    db.close()
    gs.cache_clear()
