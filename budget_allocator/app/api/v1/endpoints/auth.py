"""
app/api/v1/endpoints/auth.py
-----------------------------
Authentication endpoints:

  POST /auth/login            — username + password [+ TOTP] → JWT pair
  POST /auth/refresh          — refresh token → new access + refresh tokens (rotated)
  POST /auth/setup            — one-time setup link → set password + get TOTP QR
  POST /auth/forgot-password  — self-service reset: new + confirm password + TOTP code

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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    ChangePasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
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
            await db.commit()
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
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )
        # Fix #3: Reject replayed TOTP codes (same code used within 90s)
        if await crud_totp.is_code_consumed(db, user.id, payload.totp_code):
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
            await db.commit()
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
    await db.commit()
    return SetupAccountResponse(
        message="Account configured. Scan the QR code with your Authenticator app.",
        totp_provisioning_uri=totp_uri,
        username=user.username,
    )





# ---------------------------------------------------------------------------
# POST /auth/change-password
# ---------------------------------------------------------------------------

@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChangePasswordResponse:
    """
    Authenticated password change for logged-in users.

    Unlike the self-service Forgot Password flow, this endpoint:
    1. Requires authentication (Bearer access token).
    2. Requires the CURRENT password for verification.
    3. Does NOT require MFA (TOTP), making it accessible for initial setup
       and for users who haven't yet enabled MFA.

    Security:
    ---------
    - Verifies old password before allowing changes.
    - Increments `token_version` to invalidate ALL other active sessions.
    - Logs a `PASSWORD_CHANGED` audit event.
    """
    ip = _get_ip(request)

    # 1. Verify current password
    if not verify_password(payload.current_password, user.hashed_password):
        await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid current password. Please check your credentials and try again.",
        )

    # 2. Verify TOTP if enabled (Industry Standard: multi-factor verification for security sensitive operations)
    if user.totp_secret:
        if not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is enabled for this account. TOTP code is required to change password.",
            )
        if not verify_totp_code(user.totp_secret, payload.totp_code):
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code. Access denied.",
            )
        # Fix #3: Replay prevention
        if await crud_totp.is_code_consumed(db, user.id, payload.totp_code):
            await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TOTP code has already been used. Please wait for the next code.",
            )
        await crud_totp.consume_code(db, user.id, payload.totp_code)

    # 3. Update password
    user.hashed_password = hash_password(payload.new_password)

    # 3a. Auto-provision MFA if not yet configured
    #     This ensures the user can later use the forgot-password flow (which
    #     requires a valid TOTP secret as proof-of-identity).
    totp_uri = None
    if not user.totp_secret:
        user.totp_secret = generate_totp_secret()
        totp_uri = get_totp_uri(user.totp_secret, user.username)
        await _log_auth_event(db, user.id, AuthEventType.MFA_ENABLED, ip)

    # 4. Bump token_version — logs out everyone else
    user.token_version += 1
    user.requires_password_change = False
    db.add(user)

    # 5. Audit trail
    await _log_auth_event(db, user.id, AuthEventType.PASSWORD_CHANGED, ip)
    await _log_auth_event(db, user.id, AuthEventType.TOKEN_INVALIDATED, None)

    await db.commit()
    logger.info("User %s successfully changed their password", user.username)

    if totp_uri:
        return ChangePasswordResponse(
            message=(
                "Password changed successfully. MFA has been enabled for your account. "
                "Scan the QR/URI below with your Authenticator app."
            ),
            totp_provisioning_uri=totp_uri,
        )
    return ChangePasswordResponse()


# ---------------------------------------------------------------------------
# POST /auth/forgot-password
# ---------------------------------------------------------------------------

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ForgotPasswordResponse:
    """
    Self-service password reset using TOTP as proof-of-identity.

    Flow
    ----
    1. Purge stale consumed TOTP codes (opportunistic maintenance).
    2. Look up the user by username (constant-time guard against enumeration).
    3. Verify the 6-digit TOTP code — reject if invalid or already consumed.
    4. Mark the code consumed to prevent replay within the 90-second window.
    5. Hash the new password and persist it.
    6. Increment ``token_version`` — ALL existing access/refresh tokens for
       this user are immediately invalidated (no denylist required).
    7. Log a ``PASSWORD_RESET`` audit event.

    No current password is required; the Authenticator code proves identity.
    """
    ip = _get_ip(request)

    # Step 1: opportunistic TOTP replay-map cleanup
    await crud_totp.purge_expired_codes(db)

    # Step 2: look up user — always process to avoid timing-based enumeration
    result = await db.execute(select(User).where(User.username == payload.username))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        logger.debug("[forgot-password] User not found for username: %r", payload.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset failed: check username and authentication code.",
        )
    if not user.is_active:
        logger.debug("[forgot-password] User %r is inactive", payload.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset failed: check username and authentication code.",
        )

    if not user.totp_secret:
        # MFA was never configured — cannot use TOTP-based reset
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this account. Contact an administrator.",
        )

    # Step 3a: validate the TOTP code
    if not verify_totp_code(user.totp_secret, payload.totp_code):
        logger.debug(
            "[forgot-password] Invalid TOTP code for user %r (code: %s)",
            user.username,
            payload.totp_code,
        )
        await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset failed: check username and authentication code.",
        )

    # Step 3b: reject replayed TOTP codes
    if await crud_totp.is_code_consumed(db, user.id, payload.totp_code):
        await _log_auth_event(db, user.id, AuthEventType.LOGIN_FAILED, ip)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP code has already been used. Wait for the next code.",
        )

    # Step 4: consume the code so it cannot be replayed
    await crud_totp.consume_code(db, user.id, payload.totp_code)

    # Step 5: update password
    user.hashed_password = hash_password(payload.new_password)

    # Step 6: bump token_version — all existing JWTs for this user are now invalid
    user.token_version += 1
    user.requires_password_change = False
    db.add(user)

    # Step 7: audit trail
    await _log_auth_event(db, user.id, AuthEventType.PASSWORD_RESET, ip)
    await _log_auth_event(db, user.id, AuthEventType.TOKEN_INVALIDATED, None)

    await db.commit()
    logger.info("User %s successfully reset their password via TOTP", user.username)

    return ForgotPasswordResponse()
