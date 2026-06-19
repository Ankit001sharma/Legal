"""Scoped memory namespace for multi-tenant, per-user file storage."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from langchain_core.runnables import RunnableConfig


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    TENANT_USER = "tenant_user"


@dataclass(frozen=True)
class MemoryNamespace:
    """Filesystem scope for session and long-term memory."""

    tenant_id: str | None
    user_id: str

    @property
    def is_platform_scope(self) -> bool:
        return self.tenant_id is None


@dataclass(frozen=True)
class MemoryPaths:
    sessions_dir: Path
    auto_dir: Path


_namespace_ctx: ContextVar[MemoryNamespace | None] = ContextVar("memory_namespace", default=None)

_LEGACY_NAMESPACE = MemoryNamespace(tenant_id="_legacy", user_id="_unknown")


def set_memory_namespace(namespace: MemoryNamespace | None) -> None:
    _namespace_ctx.set(namespace)


def get_active_namespace() -> MemoryNamespace:
    ns = _namespace_ctx.get()
    if ns is not None:
        return ns
    return _LEGACY_NAMESPACE


def namespace_from_config(config: RunnableConfig | None) -> MemoryNamespace | None:
    if not config:
        return None
    cfg = config.get("configurable") or {}
    user_id = cfg.get("user_id")
    if not user_id:
        return None
    tenant_id = cfg.get("tenant_id")
    return MemoryNamespace(tenant_id=tenant_id, user_id=str(user_id))


def apply_config_namespace(config: RunnableConfig | None) -> MemoryNamespace:
    ns = namespace_from_config(config)
    if ns is not None:
        set_memory_namespace(ns)
        return ns
    return get_active_namespace()


def resolve_memory_paths(namespace: MemoryNamespace, *, memory_root: Path) -> MemoryPaths:
    if namespace.is_platform_scope:
        base = memory_root / "platform" / "users" / namespace.user_id
    else:
        base = memory_root / "tenants" / namespace.tenant_id / "users" / namespace.user_id
    return MemoryPaths(
        sessions_dir=base / "sessions",
        auto_dir=base / "auto",
    )
