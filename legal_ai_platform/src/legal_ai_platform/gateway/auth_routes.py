"""Auth API routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from legal_ai_platform.auth.dependencies import get_db, get_current_principal, require_super_admin
from legal_ai_platform.auth.jwt import create_access_token
from legal_ai_platform.auth.passwords import hash_password, verify_password
from legal_ai_platform.auth.principal import Principal, UserRole
from legal_ai_platform.config import get_settings
from legal_ai_platform.db.models import Tenant, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: UserRole = UserRole.TENANT_USER
    tenant_id: str | None = None
    tenant_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: str
    email: str
    role: UserRole
    tenant_id: str | None


def _user_to_principal(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        email=user.email,
        role=UserRole(user.role),
        tenant_id=user.tenant_id,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == body.email.lower()).one_or_none()
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        logger.info("login failed email=%s", body.email.lower())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    settings = get_settings()
    token = create_access_token(
        principal=_user_to_principal(user),
        secret=settings.jwt_secret,
        expire_minutes=settings.jwt_expire_minutes,
    )
    logger.info("login success user_id=%s email=%s", user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(principal: Principal = Depends(get_current_principal)) -> UserResponse:
    return UserResponse(
        user_id=principal.user_id,
        email=principal.email,
        role=principal.role,
        tenant_id=principal.tenant_id,
    )


@router.post("/register", response_model=UserResponse)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_super_admin),
) -> UserResponse:
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    tenant_id = body.tenant_id
    if body.role != UserRole.SUPER_ADMIN:
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id required for non-super-admin users")
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(id=tenant_id, name=body.tenant_name or tenant_id)
            db.add(tenant)
    elif tenant_id:
        raise HTTPException(status_code=400, detail="super_admin cannot have tenant_id")

    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=hash_password(body.password),
        role=body.role.value,
        tenant_id=tenant_id if body.role != UserRole.SUPER_ADMIN else None,
    )
    db.add(user)
    db.flush()
    principal = _user_to_principal(user)
    logger.info(
        "user registered user_id=%s email=%s role=%s tenant_id=%s",
        user.id,
        user.email,
        user.role,
        user.tenant_id,
    )
    return UserResponse(
        user_id=principal.user_id,
        email=principal.email,
        role=principal.role,
        tenant_id=principal.tenant_id,
    )
