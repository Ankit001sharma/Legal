"""JWT validation for retrieval MCP tool endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class ToolPrincipal:
    user_id: str
    email: str
    role: str
    tenant_id: str | None


def _auth_required() -> bool:
    return os.environ.get("AUTH_REQUIRED", "true").lower() in {"1", "true", "yes"}


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "change-me-in-production")


def decode_bearer_token(token: str) -> ToolPrincipal:
    payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    return ToolPrincipal(
        user_id=str(payload["sub"]),
        email=str(payload.get("email", "")),
        role=str(payload.get("role", "tenant_user")),
        tenant_id=payload.get("tenant_id"),
    )


def authorize_tool_request(request: Request, body_tenant_id: str | None) -> ToolPrincipal | None:
    """Validate bearer token and tenant scope for a /tools/* call."""
    if not _auth_required():
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header.split(" ", 1)[1].strip()
    try:
        principal = decode_bearer_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if principal.role == "super_admin":
        return principal

    if body_tenant_id and principal.tenant_id and body_tenant_id != principal.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id does not match authenticated principal",
        )

    return principal
