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

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import desc, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.api.dependencies.auth import get_current_admin_user
from app.core.database import get_db
from app.core.security import create_token, hash_password
from app.crud import crud_rate_card, crud_user, crud_audit
from app.models.models import AuditLog, Budget, User
from app.schemas.schemas import (
    AdminActionLogOut,
    AuditLogOut,
    PaginatedResponse,
    RateCardCreate,
    RateCardOut,
    RateCardUpdate,
    ResetLinkResponse,
    UserOut,
    UserProvisionRequest,
    UserProvisionResponse,
    UserUpdate,
)
from app.models.models import AdminActionLog, AdminActionType
from app.services.admin_logger import log_admin_action
from app.services.calculation_service import REQUIRED_RATE_KEYS

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
    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_PROVISION,
        target_id=new_user.id,
        target_name=new_user.username,
        detail={"is_admin": new_user.is_admin},
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
    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_PASSWORD_RESET,
        target_id=user.id,
        target_name=user.username,
    )
    logger.info("Admin %s reset password for user %s", admin.username, user.username)
    return ResetLinkResponse(setup_token=setup_token)


@router.get("/users", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[UserOut]:
    total = await crud_user.count_all_users(db)
    items = await crud_user.get_all_users_paginated(db, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


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
    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_ACTIVATE if is_active else AdminActionType.USER_DEACTIVATE,
        target_id=user.id,
        target_name=user.username,
        detail={"is_active": is_active},
    )
    return user  # type: ignore[return-value]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Update a user's profile (username, role, active status).

    Security-sensitive changes (is_admin, is_active) automatically
    invalidate all of the user's existing JWT tokens.
    """
    user = await crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Prevent username collision
    if payload.username and payload.username != user.username:
        existing = await crud_user.get_user_by_username(db, payload.username)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{payload.username}' is already taken",
            )

    user = await crud_user.update_user(
        db, user,
        username=payload.username,
        is_admin=payload.is_admin,
        is_active=payload.is_active,
    )
    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_UPDATE,
        target_id=user.id,
        target_name=user.username,
        detail=payload.model_dump(exclude_none=True),
    )
    logger.info("Admin %s updated user %s", admin.username, user.username)
    return user  # type: ignore[return-value]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Soft-delete a user account.

    Guards:
    - Cannot delete your own account.
    - Cannot delete a user who has active (non-deleted) budgets linked
      through the audit trail. All budgets must be reassigned or deleted first.
    """
    user = await crud_user.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # Guard: check for active budgets linked to this user via AuditLog
    budget_count_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .join(Budget, AuditLog.entity_id == Budget.id.cast(String))
        .where(
            AuditLog.user_id == user_id,
            AuditLog.entity_type == "Budget",
            Budget.is_deleted == False,  # noqa: E712
        )
    )
    active_budget_count = budget_count_result.scalar_one()
    if active_budget_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete user: {active_budget_count} active budget(s) are "
                f"associated with this user. Delete or reassign them first."
            ),
        )

    await crud_user.soft_delete_user(db, user)
    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_DELETE,
        target_id=user.id,
        target_name=user.username,
    )
    logger.info("Admin %s soft-deleted user %s", admin.username, user.username)
    return Response(status_code=204)


# ===========================================================================
# Rate Cards
# ===========================================================================


@router.get("/rate-cards", response_model=list[RateCardOut])
async def list_rate_cards(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[RateCardOut]:
    return await crud_rate_card.get_all_rate_cards(db)  # type: ignore[return-value]


@router.get("/rate-cards/required-keys", response_model=list[str])
async def get_required_rate_keys(
    _: User = Depends(get_current_admin_user),
) -> list[str]:
    """Return the list of rate card keys required for budget calculations."""
    return REQUIRED_RATE_KEYS


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

    # Guard: prevent renaming a required key to a non-required key name
    if rc.key_name in REQUIRED_RATE_KEYS and payload.key_name and payload.key_name != rc.key_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot rename required system rate card key '{rc.key_name}'",
        )

    try:
        rc = await crud_rate_card.update_rate_card(db, rc, payload)
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rate card with this key name already exists"
        )
        
    logger.info(
        "Admin %s updated rate card '%s' → %.4f", admin.username, rc.key_name, rc.value
    )
    return rc  # type: ignore[return-value]


@router.delete("/rate-cards/{rc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_card(
    rc_id: int,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    rc = await crud_rate_card.get_rate_card_by_id(db, rc_id)
    if not rc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RateCard not found")

    # Guard: prevent deletion of required system rate cards
    if rc.key_name in REQUIRED_RATE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot delete required system rate card '{rc.key_name}'",
        )

    await crud_rate_card.delete_rate_card(db, rc)
    return Response(status_code=204)


# ===========================================================================
# Audit Logs (read-only)
# ===========================================================================


from app.api.dependencies.filters import AuditFilterParams, get_audit_filters


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def get_audit_logs(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    filters: AuditFilterParams = Depends(get_audit_filters),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogOut]:
    """Return entity audit logs, optionally filtered by entity type / ID and action/actor."""
    return await crud_audit.get_audit_logs(
        db, entity_type=entity_type, entity_id=entity_id, filters=filters, limit=limit, offset=offset
    )  # type: ignore[return-value]


# ===========================================================================
# Admin Action Logs (read-only)
# ===========================================================================


@router.get("/action-logs", response_model=list[AdminActionLogOut])
async def get_admin_action_logs(
    actor_name: str | None = Query(default=None, description="Filter by admin username"),
    action: str | None = Query(default=None, description="Filter by action type e.g. USER_PROVISION"),
    target_name: str | None = Query(default=None, description="Filter by target username"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AdminActionLogOut]:
    """
    Return admin intent logs — who did what to whom as an administrator.

    Unlike /audit-logs (raw ORM diffs), these are human-readable records of
    deliberate admin decisions: provisioning, role changes, deletions, resets.
    """
    from sqlalchemy import desc
    stmt = select(AdminActionLog).order_by(desc(AdminActionLog.timestamp))
    if actor_name:
        stmt = stmt.where(AdminActionLog.actor_name.ilike(f"%{actor_name}%"))
    if action:
        stmt = stmt.where(AdminActionLog.action == action)
    if target_name:
        stmt = stmt.where(AdminActionLog.target_name.ilike(f"%{target_name}%"))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()  # type: ignore[return-value]
