"""JWT creation and validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from legal_ai_platform.auth.principal import Principal, UserRole


def create_access_token(
    *,
    principal: Principal,
    secret: str,
    expire_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": principal.user_id,
        "email": principal.email,
        "role": principal.role.value,
        "tenant_id": principal.tenant_id,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> Principal:
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    role_raw = payload.get("role", UserRole.TENANT_USER.value)
    try:
        role = UserRole(role_raw)
    except ValueError as exc:
        raise jwt.InvalidTokenError(f"Unknown role: {role_raw}") from exc
    return Principal(
        user_id=str(payload["sub"]),
        email=str(payload.get("email", "")),
        role=role,
        tenant_id=payload.get("tenant_id"),
    )
