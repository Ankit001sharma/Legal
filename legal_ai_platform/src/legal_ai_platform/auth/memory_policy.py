"""Memory session access policy for RBAC."""

from __future__ import annotations

from dataclasses import dataclass

from legal_ai_platform.auth.principal import Principal, UserRole
from legal_ai_platform.db.models import MemorySession


@dataclass(frozen=True)
class SessionAccess:
    allowed: bool
    can_write: bool
    reason: str = ""


class MemoryAccessPolicy:
    """Enforce who may read/write a registered memory session."""

    @staticmethod
    def can_access(principal: Principal, record: MemorySession) -> SessionAccess:
        if principal.user_id == record.user_id:
            return SessionAccess(allowed=True, can_write=True)

        if principal.role == UserRole.SUPER_ADMIN:
            return SessionAccess(allowed=True, can_write=True)

        if (
            principal.role == UserRole.TENANT_ADMIN
            and principal.tenant_id
            and principal.tenant_id == record.tenant_id
        ):
            return SessionAccess(allowed=True, can_write=False)

        return SessionAccess(
            allowed=False,
            can_write=False,
            reason="You do not have access to this session.",
        )

    @staticmethod
    def namespace_tenant_id(principal: Principal, request_tenant_id: str | None) -> str | None:
        if principal.role == UserRole.SUPER_ADMIN:
            return request_tenant_id
        return principal.tenant_id
