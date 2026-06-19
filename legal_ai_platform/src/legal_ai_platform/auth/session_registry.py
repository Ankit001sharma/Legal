"""Memory session registry backed by Postgres/SQLite."""

from __future__ import annotations

from sqlalchemy.orm import Session

from legal_ai_platform.auth.memory_policy import MemoryAccessPolicy, SessionAccess
from legal_ai_platform.auth.principal import Principal
from legal_ai_platform.db.models import MemorySession


class SessionRegistry:
    """Register and authorize conversation session ownership."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, session_id: str) -> MemorySession | None:
        return self._db.get(MemorySession, session_id)

    def register(
        self,
        *,
        session_id: str,
        principal: Principal,
        tenant_id: str | None,
    ) -> MemorySession:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        record = MemorySession(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=principal.user_id,
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return record

    def authorize(
        self,
        *,
        session_id: str,
        principal: Principal,
        tenant_id: str | None,
        allow_register: bool = True,
    ) -> tuple[MemorySession, SessionAccess]:
        record = self.get(session_id)
        if record is None:
            if not allow_register:
                return (
                    MemorySession(session_id=session_id, tenant_id=tenant_id, user_id=""),
                    SessionAccess(allowed=False, can_write=False, reason="Session not found."),
                )
            record = self.register(
                session_id=session_id,
                principal=principal,
                tenant_id=tenant_id,
            )
        access = MemoryAccessPolicy.can_access(principal, record)
        return record, access
