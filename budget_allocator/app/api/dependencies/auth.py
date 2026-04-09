"""
app/api/dependencies/auth.py
-----------------------------
FastAPI dependency functions for authentication and authorization.

All dependencies are async and injectable via Depends().
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token, verify_token_kind
from app.core.context import current_user_id   # Fix #10
from app.models.models import User

bearer_scheme = HTTPBearer(auto_error=True)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_user_from_token(
    token: str,
    kind: str,
    db: AsyncSession,
) -> User:
    """
    Decode *token*, validate its kind and version, then return the User.
    Raises HTTP 401 on any failure.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        verify_token_kind(payload, kind)  # type: ignore[arg-type]
        user_id_str: str | None = payload.get("sub")
        token_version: int | None = payload.get("ver")
        if user_id_str is None or token_version is None:
            raise credentials_exception
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    # Verify token version matches DB — catches post-password-change tokens
    if user.token_version != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been invalidated. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Fix #10: Populate the request-scoped context var so audit_logger can
    # record who performed the action without needing an HTTP request reference.
    current_user_id.set(user.id)
    return user


# ---------------------------------------------------------------------------
# Public dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the currently authenticated user from the Bearer access token."""
    return await _get_user_from_token(credentials.credentials, "access", db)


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Raise HTTP 403 if the authenticated user is not an admin.
    Use this dependency on any admin-only endpoint.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return current_user


async def get_setup_token_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate a one-time setup/reset link token and return the target User.
    Used on POST /auth/setup and POST /auth/reset-password.
    """
    return await _get_user_from_token(credentials.credentials, "setup", db)
