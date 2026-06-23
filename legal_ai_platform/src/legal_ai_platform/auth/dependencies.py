"""FastAPI auth dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from legal_ai_platform.auth.jwt import decode_access_token
from legal_ai_platform.auth.memory_policy import MemoryAccessPolicy
from legal_ai_platform.auth.principal import Principal, UserRole
from legal_ai_platform.auth.session_registry import SessionRegistry
from legal_ai_platform.config import get_settings
from legal_ai_platform.db.session import get_session_factory
from legal_ai_platform.models.agent import AgentRequest

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Session:
    settings = get_settings()
    factory = get_session_factory(settings.database_url)
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _token_from_credentials(
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    settings = get_settings()
    if not settings.auth_required:
        return Principal(
            user_id=settings.dev_anonymous_user_id,
            email="anonymous@local",
            role=UserRole.TENANT_USER,
            tenant_id=settings.dev_anonymous_tenant_id,
        )

    token = _token_from_credentials(credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(token, settings.jwt_secret)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_optional_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str | None:
    return _token_from_credentials(credentials)


def enrich_agent_request(
    body: AgentRequest,
    principal: Principal,
    db: Session,
    auth_token: str | None = None,
) -> AgentRequest:
    """Resolve tenant, authorize session, and attach principal fields.

    When running as an internal service (auth_required=false) the Java gateway
    passes real user context in the request body (user_id, role, tenant_id).
    In that case we build the effective principal from the body rather than
    using the anonymous dev fallback, so per-user memory isolation still works.
    """
    settings = get_settings()

    # Service-mode: trust body-provided user context from Java gateway
    if not settings.auth_required and body.user_id:
        try:
            body_role = UserRole(body.role) if body.role else UserRole.TENANT_USER
        except ValueError:
            body_role = UserRole.TENANT_USER
        effective_principal = Principal(
            user_id=body.user_id,
            email=f"{body.user_id}@service",
            role=body_role,
            tenant_id=body.tenant_id,
        )
    else:
        effective_principal = principal

    resolved_tenant = MemoryAccessPolicy.namespace_tenant_id(effective_principal, body.tenant_id)
    session_id = body.session_id
    if session_id and settings.auth_required:
        registry = SessionRegistry(db)
        _, access = registry.authorize(
            session_id=session_id,
            principal=effective_principal,
            tenant_id=resolved_tenant,
        )
        if not access.allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access.reason)
    elif session_id:
        registry = SessionRegistry(db)
        registry.authorize(
            session_id=session_id,
            principal=effective_principal,
            tenant_id=resolved_tenant,
        )

    return body.model_copy(
        update={
            "tenant_id": resolved_tenant,
            "user_id": effective_principal.user_id,
            "role": effective_principal.role.value,
            "context": {
                **body.context,
                "principal_email": effective_principal.email,
                **({"auth_token": auth_token} if auth_token else {}),
            },
        }
    )


def require_super_admin(principal: Annotated[Principal, Depends(get_current_principal)]) -> Principal:
    if principal.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin required")
    return principal
