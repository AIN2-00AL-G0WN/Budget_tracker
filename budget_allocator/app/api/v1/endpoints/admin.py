"""
app/api/v1/endpoints/admin.py
------------------------------
HTTP Controller for admin-only operations.

This router is intentionally thin: it validates schemas via Pydantic, delegates
ALL database operations to the appropriate CRUD module, and raises HTTP
exceptions when the CRUD layer signals a missing or conflicting resource.

Route summary
~~~~~~~~~~~~~
  POST   /admin/users/provision       — create a new user + return setup token
  POST   /admin/users/{id}/reset      — force-reset a user's password
  GET    /admin/users                 — list all users
  PATCH  /admin/users/{id}/activate   — toggle active state for a user
  DELETE /admin/users/{id}            — (reserved: use PATCH /activate instead)

  GET    /admin/rate-cards            — list all rate card entries
  POST   /admin/rate-cards            — create a new rate card entry
  PATCH  /admin/rate-cards/{id}       — update a rate card value
  DELETE /admin/rate-cards/{id}       — remove a rate card entry

  GET    /admin/audit-logs            — paginated audit trail
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user
from app.core.database import get_db
from app.core.security import create_token, hash_password
from app.crud import crud_rate_card, crud_user
from app.models.models import AuditLog, User
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

# A placeholder hash — the user MUST set a real password via the setup link.
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
    2. Admin shares ``setup_token`` with the new manager via a secure channel.
    3. Manager hits POST /auth/setup with the token to set their password and
       scan their TOTP QR code.
    """
    # Uniqueness guard
    existing = await crud_user.get_user_by_username(db, payload.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' is already taken",
        )

    new_user = await crud_user.create_user(
        db,
        username=payload.username,
        hashed_password=hash_password(_PLACEHOLDER_PW),
        is_admin=payload.is_admin,
        is_active=True,
        requires_password_change=True,
        token_version=0,
    )

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

    Increments ``token_version`` so all existing tokens are immediately invalid.
    """
    user = await crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user = await crud_user.bump_token_version_for_reset(db, user)

    setup_token = create_token(
        user_id=user.id,
        kind="setup",
        token_version=user.token_version,
    )
    logger.info("Admin %s reset password for user %s", admin.username, user.username)
    return ResetLinkResponse(setup_token=setup_token)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    return await crud_user.get_all_users(db)  # type: ignore[return-value]


@router.patch("/users/{user_id}/activate", response_model=UserOut)
async def toggle_user_active(
    user_id: uuid.UUID,
    is_active: bool = Query(..., description="True to activate, False to deactivate"),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )
    user = await crud_user.set_active(db, user, is_active=is_active)
    return user  # type: ignore[return-value]


# ===========================================================================
# Rate Cards
# ===========================================================================


@router.get("/rate-cards", response_model=list[RateCardOut])
async def list_rate_cards(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[RateCardOut]:
    return await crud_rate_card.get_all_rate_cards(db)  # type: ignore[return-value]


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
    existing = await crud_rate_card.get_rate_card_by_key(db, payload.key_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RateCard key '{payload.key_name}' already exists — use PATCH to update",
        )
    rc = await crud_rate_card.create_rate_card(db, payload)
    logger.info("Admin %s created rate card '%s'=%.4f", admin.username, rc.key_name, rc.value)
    return rc  # type: ignore[return-value]


@router.patch("/rate-cards/{rc_id}", response_model=RateCardOut)
async def update_rate_card(
    rc_id: int,
    payload: RateCardUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> RateCardOut:
    rc = await crud_rate_card.get_rate_card_by_id(db, rc_id)
    if not rc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RateCard not found")
    rc = await crud_rate_card.update_rate_card(db, rc, payload)
    logger.info(
        "Admin %s updated rate card '%s' → %.4f", admin.username, rc.key_name, rc.value
    )
    return rc  # type: ignore[return-value]


@router.delete("/rate-cards/{rc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_card(
    rc_id: int,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    rc = await crud_rate_card.get_rate_card_by_id(db, rc_id)
    if not rc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RateCard not found")
    await crud_rate_card.delete_rate_card(db, rc)


# ===========================================================================
# Audit Logs (read-only)
# ===========================================================================


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def get_audit_logs(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogOut]:
    """Return audit logs, optionally filtered by entity type / ID."""
    # AuditLog is append-only and has no mutation operations, so its query
    # stays directly in this router rather than in a dedicated CRUD module.
    q = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()  # type: ignore[return-value]
