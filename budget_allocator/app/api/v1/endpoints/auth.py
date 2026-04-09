"""
app/api/v1/endpoints/auth.py
-----------------------------
Authentication endpoints:

  POST /auth/login            — username + password [+ TOTP] → JWT pair
  POST /auth/refresh          — refresh token → new access + refresh tokens (rotated)
  POST /auth/setup            — one-time setup link → set password + get TOTP QR
  POST /auth/change-password  — authenticated user changes own password

Security fixes applied
----------------------
Fix #5  — Timing oracle: ``verify_password`` is now called even when the user
           does not exist (constant-time dummy hash), preventing username
           enumeration via response-time difference.
Fix #3  — TOTP replay: successfully verified TOTP codes are recorded in
           ``consumed_totp_codes`` for 90 seconds and rejected if replayed.
Fix #6  — Refresh token rotation: ``POST /auth/refresh`` bumps ``token_version``
           before minting new tokens, silently invalidating the old pair.
Fix #16 — Dead code removed from refresh endpoint (spurious User-by-username
           query that always returned None).
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
from app.crud import crud_totp, crud_user
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
# Fix #5: Pre-computed dummy hash used when the username is not found.
# verify_password is always called so the response time is indistinguishable
# whether the user exists or not, preventing timing-based user enumeration.
# ---------------------------------------------------------------------------
_DUMMY_HASH: str = hash_password("dummy-timing-prevention-string-xK9!")


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
    1. Opportunistically purge expired consumed TOTP codes (maintenance).
    2. Look up the user by username.
    3. Always verify a password hash (Fix #5 — constant time, no enumeration).
    4. If the user has TOTP enabled, validate the code and check replay (Fix #3).
    5. Mint and return tokens.
    """
    ip = _get_ip(request)

    # Fix #3: Purge stale replay-prevention records opportunistically
    await crud_totp.purge_expired_codes(db)

    result = await db.execute(select(User).where(User.username == payload.username))
    user: User | None = result.scalar_one_or_none()

    # Fix #5: Always call verify_password — prevents timing oracle.
    # If the user doesn't exist, verify against the dummy hash (always False).
    candidate_hash = user.hashed_password if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, candidate_hash)

    if not user or not password_ok:
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
        # Fix #3: Reject replayed TOTP codes (same code used within 90s)
        if await crud_totp.is_code_consumed(db, user.id, payload.totp_code):
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code has already been used. Wait for the next code.",
            )
        # Mark code as consumed so it cannot be replayed
        await crud_totp.consume_code(db, user.id, payload.totp_code)

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
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    Fix #6 — Refresh Token Rotation
    --------------------------------
    On every successful refresh, ``token_version`` is incremented.  This
    immediately invalidates the old refresh token (it now carries a stale
    ``ver`` claim) enforcing single-use semantics without a token denylist.

    Fix #16 — Dead Code
    --------------------
    Removed the spurious ``select(User).where(User.username == user_id)``
    query that always returned None (user_id is a UUID string, not a username).
    """
    import uuid as _uuid

    try:
        data = decode_token(payload.refresh_token)
        if data.get("kind") != "refresh":
            raise ValueError("Not a refresh token")
        user_id_str: str = data["sub"]
        token_ver: int = data["ver"]
        user_id = _uuid.UUID(user_id_str)   # Fix #16: parse UUID directly
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Fix #16: Single targeted query — no spurious username lookup
    user: User | None = await crud_user.get_user_by_id(db, user_id)

    if user is None or not user.is_active or user.token_version != token_ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalidated — please log in again",
        )

    # Fix #6: Rotate token version — old refresh token is now permanently invalid
    user = await crud_user.rotate_token_version(db, user)

    new_access = create_token(
        user_id=user.id,
        kind="access",
        token_version=user.token_version,  # new version
        username=user.username,
        is_admin=user.is_admin,
    )
    new_refresh = create_token(
        user_id=user.id,
        kind="refresh",
        token_version=user.token_version,  # new version
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
    3. Mark ``requires_password_change = False``.
    4. Increment ``token_version`` to invalidate the setup token immediately.
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
    * Increments ``token_version`` — IMMEDIATELY invalidates all existing JWT
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
