"""
app/api/v1/endpoints/auth.py
-----------------------------
Authentication endpoints:

  POST /auth/login            — username + password [+ TOTP] → JWT pair
  POST /auth/refresh          — refresh token → new access token
  POST /auth/setup            — one-time setup link → set password + get TOTP QR
  POST /auth/change-password  — authenticated user changes own password
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, get_setup_token_user
from app.core.database import get_db
from app.core.security import (
    create_token,
    decode_token,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    verify_password,
    verify_totp_code,
)
from app.models.models import AuthEventType, AuthLog, User
from app.schemas.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    SetupAccountRequest,
    SetupAccountResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _log_auth_event(
    db: AsyncSession,
    user_id,
    event_type: AuthEventType,
    ip: str | None,
) -> None:
    db.add(AuthLog(user_id=user_id, event_type=event_type, ip_address=ip))
    # Flush without committing — the caller's session.commit() will persist it


def _get_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate a user and return a JWT access + refresh token pair.

    Flow
    ----
    1. Look up the user by username.
    2. Verify the Argon2id password hash.
    3. If the user has TOTP enabled (`totp_secret` is set), validate the code.
    4. Mint and return tokens.
    """
    ip = _get_ip(request)

    result = await db.execute(select(User).where(User.username == payload.username))
    user: User | None = result.scalar_one_or_none()

    def _fail_login():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if user is None or not verify_password(payload.password, user.hashed_password):
        if user:
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    # TOTP check — only enforced once MFA setup is complete
    if user.totp_secret:
        if not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code required for this account",
            )
        if not verify_totp_code(user.totp_secret, payload.totp_code):
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

    await _log_auth_event(db, user.id, AuthEventType.LOGIN_SUCCESS, ip)

    access_token = create_token(
        user_id=user.id,
        kind="access",
        token_version=user.token_version,
        username=user.username,
        is_admin=user.is_admin,
    )
    refresh_token = create_token(
        user_id=user.id,
        kind="refresh",
        token_version=user.token_version,
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchange a valid refresh token for a new access token."""
    from jose import JWTError

    try:
        data = decode_token(payload.refresh_token)
        if data.get("kind") != "refresh":
            raise ValueError("Not a refresh token")
        user_id = data["sub"]
        token_ver = data["ver"]
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    result = await db.execute(select(User).where(User.username == user_id))
    # User lookup by ID string
    from sqlalchemy import text
    import uuid as _uuid
    result2 = await db.execute(select(User).where(User.id == _uuid.UUID(user_id)))
    user: User | None = result2.scalar_one_or_none()

    if user is None or not user.is_active or user.token_version != token_ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalidated — please log in again",
        )

    new_access = create_token(
        user_id=user.id,
        kind="access",
        token_version=user.token_version,
        username=user.username,
        is_admin=user.is_admin,
    )
    new_refresh = create_token(
        user_id=user.id,
        kind="refresh",
        token_version=user.token_version,
    )
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


# ---------------------------------------------------------------------------
# POST /auth/setup  (one-time provisioning link)
# ---------------------------------------------------------------------------

@router.post("/setup", response_model=SetupAccountResponse)
async def setup_account(
    payload: SetupAccountRequest,
    user: User = Depends(get_setup_token_user),
    db: AsyncSession = Depends(get_db),
) -> SetupAccountResponse:
    """
    Complete account provisioning:
    1. Set the user's password (Argon2id).
    2. Generate a fresh TOTP secret and return the provisioning URI.
    3. Mark `requires_password_change = False`.
    4. Increment `token_version` to invalidate the setup token immediately.
    """
    user.hashed_password = hash_password(payload.new_password)
    user.totp_secret = generate_totp_secret()
    user.requires_password_change = False
    user.token_version += 1   # Invalidates the setup token — can't reuse

    db.add(user)
    await _log_auth_event(db, user.id, AuthEventType.MFA_ENABLED, None)

    totp_uri = get_totp_uri(user.totp_secret, user.username)
    return SetupAccountResponse(
        message="Account configured. Scan the QR code with your Authenticator app.",
        totp_provisioning_uri=totp_uri,
        username=user.username,
    )


# ---------------------------------------------------------------------------
# POST /auth/change-password
# ---------------------------------------------------------------------------

@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Authenticated user changes their own password.

    On success:
    * Hashes the new password with Argon2id.
    * Increments `token_version` — IMMEDIATELY invalidates all existing JWT
      access and refresh tokens for this user (no Redis denylist needed).
    """
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from current password",
        )

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.token_version += 1    # ← All old tokens are now invalid
    current_user.requires_password_change = False
    db.add(current_user)

    await _log_auth_event(
        db, current_user.id, AuthEventType.PASSWORD_CHANGED, _get_ip(request)
    )
    await _log_auth_event(
        db, current_user.id, AuthEventType.TOKEN_INVALIDATED, None
    )
