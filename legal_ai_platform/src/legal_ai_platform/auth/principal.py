"""Authenticated principal and role definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    TENANT_USER = "tenant_user"


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: UserRole
    tenant_id: str | None

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN

    @property
    def is_tenant_admin(self) -> bool:
        return self.role == UserRole.TENANT_ADMIN

    def resolved_tenant_id(self, request_tenant_id: str | None = None) -> str | None:
        """Return the effective tenant scope for this request."""
        if self.is_super_admin:
            return request_tenant_id
        return self.tenant_id
