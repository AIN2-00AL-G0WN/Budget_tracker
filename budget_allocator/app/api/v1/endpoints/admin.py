"""
app/api/v1/endpoints/admin.py
------------------------------
Admin-only endpoints:

  POST   /users/provision          — create a new user + return setup token
  POST   /users/{id}/reset         — force-reset a user's password (new setup link)
  GET    /users                    — list all users
  PATCH  /users/{id}/activate      — re-activate a disabled user
  DELETE /users/{id}               — soft-deactivate a user

  GET    /rate-cards               — list all rate card entries
  POST   /rate-cards               — create a new rate card entry
  PATCH  /rate-cards/{id}          — update a rate card value
  DELETE /rate-cards/{id}          — remove a rate card entry

  GET    /audit-logs               — paginated audit trail
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user
from app.core.database import get_db
from app.core.security import create_token, hash_password
from app.models.models import AuditLog, Notification, RateCard, User
from app.schemas.schemas import (
    AuditLogOut,
    RateCardCreate,
    RateCardOut,
    RateCardUpdate,
    ResetLinkResponse,
    UserOut,
    UserProvisionRequest,
    UserProvisionResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

# A random 16-char placeholder hash — the user MUST set a real password via the setup link
_PLACEHOLDER_PW = "!" * 16


# ===========================================================================
# User management
# ===========================================================================

@router.post(
    "/users/provision",
    response_model=UserProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_user(
    payload: UserProvisionRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserProvisionResponse:
    """
    Create a new user account and return a single-use JWT setup link.

    Offline onboarding workflow
    ---------------------------
    1. Admin calls this endpoint.
    2. Admin shares the `setup_token` with the new manager via a secure channel
       (e.g. face-to-face, encrypted message).
    3. Manager hits POST /auth/setup with the token as a Bearer header to set
       their password and scan their TOTP QR code.
    """
    # Check username uniqueness
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' is already taken",
        )

    new_user = User(
        username=payload.username,
        hashed_password=hash_password(_PLACEHOLDER_PW),  # Invalid — forces setup
        is_admin=payload.is_admin,
        is_active=True,
        requires_password_change=True,
        token_version=0,
    )
    db.add(new_user)
    await db.flush()   # Get the UUID before we mint the token

    setup_token = create_token(
        user_id=new_user.id,
        kind="setup",
        token_version=new_user.token_version,
    )
    logger.info("Admin %s provisioned user %s", admin.username, new_user.username)

    return UserProvisionResponse(
        user_id=new_user.id,
        username=new_user.username,
        setup_token=setup_token,
    )


@router.post("/users/{user_id}/reset", response_model=ResetLinkResponse)
async def reset_user_password(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ResetLinkResponse:
    """
    Generate a new single-use setup token for a user (password reset).
    Increments token_version so all existing tokens are immediately invalid.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.token_version += 1          # Invalidate all existing tokens
    user.requires_password_change = True
    db.add(user)
    await db.flush()

    setup_token = create_token(
        user_id=user.id,
        kind="setup",
        token_version=user.token_version,
    )
    logger.info("Admin %s reset password for user %s", admin.username, user.username)
    return ResetLinkResponse(setup_token=setup_token)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()  # type: ignore[return-value]


@router.patch("/users/{user_id}/activate", response_model=UserOut)
async def toggle_user_active(
    user_id: uuid.UUID,
    is_active: bool = Query(..., description="True to activate, False to deactivate"),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )
    user.is_active = is_active
    if not is_active:
        user.token_version += 1   # Force logout
    db.add(user)
    return user  # type: ignore[return-value]


# ===========================================================================
# Rate Cards
# ===========================================================================

@router.get("/rate-cards", response_model=list[RateCardOut])
async def list_rate_cards(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[RateCardOut]:
    result = await db.execute(select(RateCard).order_by(RateCard.key_name))
    return result.scalars().all()  # type: ignore[return-value]


@router.post(
    "/rate-cards",
    response_model=RateCardOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_rate_card(
    payload: RateCardCreate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> RateCardOut:
    existing = await db.execute(
        select(RateCard).where(RateCard.key_name == payload.key_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RateCard key '{payload.key_name}' already exists — use PATCH to update",
        )
    rc = RateCard(**payload.model_dump())
    db.add(rc)
    await db.flush()
    logger.info("Admin %s created rate card '%s'=%.4f", admin.username, rc.key_name, rc.value)
    return rc  # type: ignore[return-value]


@router.patch("/rate-cards/{rc_id}", response_model=RateCardOut)
async def update_rate_card(
    rc_id: int,
    payload: RateCardUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> RateCardOut:
    result = await db.execute(select(RateCard).where(RateCard.id == rc_id))
    rc: RateCard | None = result.scalar_one_or_none()
    if not rc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RateCard not found")

    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(rc, field, val)
    db.add(rc)
    logger.info(
        "Admin %s updated rate card '%s' → %.4f", admin.username, rc.key_name, rc.value
    )
    return rc  # type: ignore[return-value]


@router.delete("/rate-cards/{rc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_card(
    rc_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(RateCard).where(RateCard.id == rc_id))
    rc: RateCard | None = result.scalar_one_or_none()
    if not rc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RateCard not found")
    await db.delete(rc)


# ===========================================================================
# Audit Logs (read-only)
# ===========================================================================

@router.get("/audit-logs", response_model=list[AuditLogOut])
async def get_audit_logs(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogOut]:
    """Return audit logs, optionally filtered by entity type / ID."""
    q = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()  # type: ignore[return-value]
