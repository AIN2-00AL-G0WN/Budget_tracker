"""
app/crud/crud_user.py
---------------------
Data-Access Layer (Repository) for the User entity.

All functions are async and accept an ``AsyncSession``.
They return ORM objects or None — they NEVER raise HTTP exceptions.

Note on security-sensitive operations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Functions like ``create_user`` accept raw primitives (``hashed_password``,
``token_version`` …) because the call site in the router already holds the
security module.  The CRUD layer is intentionally security-agnostic.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Fetch a single User by primary key."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Fetch a single User by unique username (used for login and uniqueness checks)."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_all_users(db: AsyncSession) -> Sequence[User]:
    """Return every user ordered by creation date (oldest first)."""
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


async def count_all_users(db: AsyncSession) -> int:
    """Return the total count of all users."""
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def get_all_users_paginated(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[User]:
    """Return a paginated slice of users ordered by creation date."""
    result = await db.execute(
        select(User).order_by(User.created_at).limit(limit).offset(offset)
    )
    return result.scalars().all()


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    hashed_password: str,
    is_admin: bool = True,
    is_active: bool = True,
    requires_password_change: bool = True,
    token_version: int = 0,
) -> User:
    """
    Persist a new User row.

    ``db.flush()`` populates ``id`` and ``created_at`` before returning so
    the caller can immediately mint tokens without a separate SELECT.
    """
    user = User(
        username=username,
        hashed_password=hashed_password,
        is_admin=is_admin,
        is_active=is_active,
        requires_password_change=requires_password_change,
        token_version=token_version,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)   # Fix #15: reload created_at / server defaults
    return user


async def set_active(db: AsyncSession, user: User, *, is_active: bool) -> User:
    """
    Toggle ``is_active`` for a user.

    When deactivating (``is_active=False``), ``token_version`` is incremented
    to invalidate all outstanding JWTs for that user immediately.
    """
    user.is_active = is_active
    if not is_active:
        user.token_version += 1
    db.add(user)
    return user


async def bump_token_version_for_reset(db: AsyncSession, user: User) -> User:
    """
    Increment ``token_version`` and mark ``requires_password_change=True``.

    Used by the admin password-reset flow to invalidate all existing tokens
    before a new setup link is issued.
    """
    user.token_version += 1
    user.requires_password_change = True
    db.add(user)
    await db.flush()
    return user


async def rotate_token_version(db: AsyncSession, user: User) -> User:
    """
    Increment ``token_version`` WITHOUT changing ``requires_password_change``.

    Used by the refresh-token rotation flow (Fix #6): every successful token
    refresh bumps the version, which silently invalidates the old refresh token
    and all old access tokens.  Old tokens present a stale ``ver`` claim that
    no longer matches the DB, so ``_get_user_from_token`` rejects them.
    """
    user.token_version += 1
    db.add(user)
    await db.flush()
    return user


async def update_user(
    db: AsyncSession,
    user: User,
    *,
    username: str | None = None,
    is_admin: bool | None = None,
    is_active: bool | None = None,
) -> User:
    """
    Update mutable user profile fields.

    Increments ``token_version`` to invalidate existing JWTs whenever
    security-sensitive fields change (is_admin, is_active).
    """
    if username is not None:
        user.username = username
    if is_admin is not None:
        user.is_admin = is_admin
        user.token_version += 1  # role change → invalidate tokens
    if is_active is not None:
        user.is_active = is_active
        if not is_active:
            user.token_version += 1  # deactivation → invalidate tokens
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def soft_delete_user(db: AsyncSession, user: User) -> None:
    """
    Soft-delete a user by deactivating and clearing their credentials.

    This is a non-reversible administrative action. The user's tokens
    are immediately invalidated.
    """
    user.is_active = False
    user.token_version += 1
    user.totp_secret = None
    db.add(user)
    await db.flush()
