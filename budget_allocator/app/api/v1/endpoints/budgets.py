"""
app/api/v1/endpoints/budgets.py
---------------------------------
Budget CRUD endpoints + notification polling.

  POST   /budgets              — submit manual inputs; server calculates everything
  GET    /budgets/{id}         — retrieve a single budget record
  PATCH  /budgets/{id}         — recalculate after input change
  DELETE /budgets/{id}         — remove budget record

  GET    /notifications        — poll in-app notifications for the current user
  POST   /notifications/mark-read  — mark one or more notifications as read
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.database import get_db
from app.models.models import Budget, Notification, SubDivision, User
from app.schemas.schemas import (
    BudgetCreate,
    BudgetOut,
    NotificationMarkRead,
    NotificationOut,
)
from app.services.calculation_service import compute_and_get_budget_fields

router = APIRouter(tags=["budgets"])
logger = logging.getLogger(__name__)


# ===========================================================================
# Budgets
# ===========================================================================

@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    payload: BudgetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetOut:
    """
    Submit `tc_count` and `duration_in_days`.

    The server:
    1. Fetches live multipliers from the RateCards table.
    2. Runs the hardcoded calculation engine.
    3. Persists the fully-calculated Budget row.
    """
    # Validate SubDivision exists
    sd_result = await db.execute(
        select(SubDivision).where(SubDivision.id == payload.sub_division_id)
    )
    if not sd_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SubDivision not found",
        )

    # Guard: one budget per sub-division
    existing = await db.execute(
        select(Budget).where(Budget.sub_division_id == payload.sub_division_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A budget already exists for this SubDivision. "
                "Use PATCH /budgets/{id} to update it."
            ),
        )

    # Run calculation
    try:
        calculated = await compute_and_get_budget_fields(
            tc_count=payload.tc_count,
            duration_in_days=payload.duration_in_days,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    budget = Budget(
        sub_division_id=payload.sub_division_id,
        tc_count=payload.tc_count,
        duration_in_days=payload.duration_in_days,
        **calculated,
    )
    db.add(budget)
    await db.flush()
    logger.info(
        "Budget created by %s for sub_division=%s: total=%.2f",
        current_user.username,
        payload.sub_division_id,
        budget.total_budget,
    )
    return budget  # type: ignore[return-value]


@router.get("/budgets/{budget_id}", response_model=BudgetOut)
async def get_budget(
    budget_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetOut:
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget  # type: ignore[return-value]


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: uuid.UUID,
    payload: BudgetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetOut:
    """
    Re-submit manual inputs to recalculate the entire budget.
    All calculated fields are overwritten — partial updates are not supported
    for calculated columns (they would violate formula consistency).
    """
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget: Budget | None = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")

    try:
        calculated = await compute_and_get_budget_fields(
            tc_count=payload.tc_count,
            duration_in_days=payload.duration_in_days,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    budget.tc_count = payload.tc_count
    budget.duration_in_days = payload.duration_in_days
    for field, val in calculated.items():
        setattr(budget, field, val)

    db.add(budget)
    logger.info(
        "Budget %s updated by %s: total=%.2f", budget_id, current_user.username, budget.total_budget
    )
    return budget  # type: ignore[return-value]


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    await db.delete(budget)


# ===========================================================================
# Notifications  (in-app bell)
# ===========================================================================

@router.get("/notifications", response_model=list[NotificationOut])
async def get_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    """Return all notifications for the current user, newest first."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(desc(Notification.created_at))
        .limit(100)
    )
    return result.scalars().all()  # type: ignore[return-value]


@router.post("/notifications/mark-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notifications_read(
    payload: NotificationMarkRead,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Mark a list of notification IDs as read for the current user."""
    result = await db.execute(
        select(Notification).where(
            Notification.id.in_(payload.notification_ids),
            Notification.user_id == current_user.id,
        )
    )
    notifications = result.scalars().all()
    for n in notifications:
        n.is_read = True
        db.add(n)
