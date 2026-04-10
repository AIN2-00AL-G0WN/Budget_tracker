"""
app/api/v1/endpoints/budgets.py
---------------------------------
HTTP Controller for Budget resources and in-app Notifications.

This router is intentionally thin: it validates schemas via Pydantic, delegates
ALL database operations to ``app.crud.crud_budget`` and
``app.crud.crud_notification``, calls ``calculation_service`` for formula
execution, and raises HTTP exceptions when the CRUD layer returns None or
signals a conflict.

Route summary
~~~~~~~~~~~~~
  POST   /budgets                    — submit manual inputs; server calculates everything
  GET    /budgets/{id}               — retrieve a single budget record
  PATCH  /budgets/{id}               — recalculate after input change
  DELETE /budgets/{id}               — remove budget record

  GET    /notifications              — poll in-app notifications for the current user
  POST   /notifications/mark-read    — mark one or more notifications as read
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.database import get_db
from app.crud import crud_budget, crud_notification, crud_project
from app.models.models import User
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
    Submit ``tc_count`` and ``duration_in_days``.

    The server:
    1. Validates that the target SubDivision exists.
    2. Enforces the one-budget-per-subdivision rule.
    3. Fetches live multipliers from the RateCards table.
    4. Runs the hardcoded calculation engine.
    5. Persists the fully-calculated Budget row.
    """
    # 1. Validate SubDivision exists
    sd = await crud_project.get_subdivision_by_id(db, payload.sub_division_id)
    if not sd:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SubDivision not found",
        )

    # 2. Guard: one budget per sub-division
    existing = await crud_budget.get_budget_for_subdivision(db, payload.sub_division_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A budget already exists for this SubDivision. "
                "Use PATCH /budgets/{id} to update it."
            ),
        )

    # 3 & 4. Run calculation engine (raises ValueError on bad rate-card config)
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

    # 5. Persist
    budget = await crud_budget.create_budget(
        db,
        sub_division_id=payload.sub_division_id,
        tc_count=payload.tc_count,
        duration_in_days=payload.duration_in_days,
        calculated_fields=calculated,
    )
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
    budget = await crud_budget.get_budget_by_id(db, budget_id)
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
    budget = await crud_budget.get_budget_by_id(db, budget_id)
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

    budget = await crud_budget.update_budget(
        db,
        budget,
        tc_count=payload.tc_count,
        duration_in_days=payload.duration_in_days,
        calculated_fields=calculated,
    )
    logger.info(
        "Budget %s updated by %s: total=%.2f",
        budget_id,
        current_user.username,
        budget.total_budget,
    )
    return budget  # type: ignore[return-value]


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    budget = await crud_budget.get_budget_by_id(db, budget_id)
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    await crud_budget.delete_budget(db, budget)
    return Response(status_code=204)


# ===========================================================================
# Notifications  (in-app bell)
# ===========================================================================


@router.get("/notifications", response_model=list[NotificationOut])
async def get_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    """Return all notifications for the current user, newest first."""
    return await crud_notification.get_notifications_for_user(db, current_user.id)  # type: ignore[return-value]


@router.post("/notifications/mark-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notifications_read(
    payload: NotificationMarkRead,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Mark a list of notification IDs as read for the current user."""
    await crud_notification.mark_notifications_read(
        db,
        current_user.id,
        payload.notification_ids,
    )
    return Response(status_code=204)
