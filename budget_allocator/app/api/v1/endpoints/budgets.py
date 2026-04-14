"""
app/api/v1/endpoints/budgets.py
---------------------------------
HTTP Controller for Budget resources and in-app Notifications.

This router is intentionally thin: it validates schemas via Pydantic, delegates
ALL database operations to ``app.crud.crud_budget`` and
``app.crud.crud_notification``, calls ``calculation_service`` for formula
execution, and raises HTTP exceptions when the CRUD layer returns None or
signals a conflict.
"""

from __future__ import annotations

import logging
import uuid

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.database import get_db
from app.crud import crud_budget, crud_notification, crud_test_run
from app.models.models import User
from app.schemas.schemas import (
    BudgetCreate,
    BudgetUpdate,
    BudgetOut,
    NotificationMarkRead,
    NotificationOut,
    PaginatedResponse,
)
from app.api.dependencies.filters import BudgetFilterParams, get_budget_filters
from app.services.calculation_service import compute_and_get_budget_fields
from app.services import export_service

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
    1. Validates that the target TestRun exists.
    2. Enforces the one-budget-per-testrun rule.
    3. Handles template inheritance (cloning previous budget for the same team).
    4. Fetches live multipliers from the RateCards table.
    5. Runs the hardcoded calculation engine.
    6. Persists the fully-calculated Budget row.
    """
    # 1. Validate TestRun exists
    tr = await crud_test_run.get_test_run_by_id(db, payload.test_run_id)
    if not tr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TestRun not found",
        )

    # 2. Guard: one budget per test_run
    existing = await crud_budget.get_budget_for_test_run(db, payload.test_run_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A budget already exists for this TestRun. "
                "Use PATCH /budgets/{id} to update it."
            ),
        )

    # Template Inheritance (Cloning)
    tc_count = payload.tc_count
    duration = payload.duration_in_days
    overrides = {
        k: v for k, v in payload.model_dump().items()
        if k.endswith("_override") and v is not None
    }

    if tc_count is None or duration is None or not overrides:
        previous = await crud_budget.get_previous_budget_for_test_run(db, payload.test_run_id)
        if previous:
            if tc_count is None:
                tc_count = previous.tc_count
            if duration is None:
                duration = previous.duration_in_days
            for k in [
                "manual_tc_multiplier_override", "automation_tc_multiplier_override",
                "adhoc_request_multiplier_override", "working_days_per_week_override",
                "hrs_per_wk_per_hc_override", "manual_hc_divisor_override",
                "automation_hc_divisor_override", "hc_rate_card_override",
                "sqpm_boise_pct_override", "pl_pct_override", "per_wqe_pct_override",
                "asqpm_pct_override", "lab_tech_manager_pct_override", "project_manager_pct_override"
            ]:
                if overrides.get(k) is None:
                    prev_val = getattr(previous, k, None)
                    if prev_val is not None:
                        overrides[k] = prev_val

    if tc_count is None or duration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tc_count and duration_in_days are required for the first budget of a team."
        )

    # 4 & 5. Run calculation engine (raises ValueError on bad rate-card config)
    try:
        calculated = await compute_and_get_budget_fields(
            tc_count=tc_count,
            duration_in_days=duration,
            db=db,
            overrides=overrides or None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # 6. Persist (write calculated results + the override columns themselves)
    budget = await crud_budget.create_budget(
        db,
        test_run_id=payload.test_run_id,
        full_budget_data=calculated,
    )
    logger.info(
        "Budget created by %s for test_run=%s: total=%.2f",
        current_user.username,
        payload.test_run_id,
        budget.total_budget,
    )
    return budget  # type: ignore[return-value]


@router.get("/budgets", response_model=PaginatedResponse[BudgetOut])
async def list_budgets(
    test_run_id: uuid.UUID | None = Query(None, description="Filter by parent test run ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BudgetOut]:
    total = await crud_budget.count_active_budgets(db, test_run_id=test_run_id, filters=filters)
    items = await crud_budget.get_budgets_paginated(db, test_run_id=test_run_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

@router.get("/budgets/export")
async def export_budgets(
    test_run_id: uuid.UUID | None = Query(None, description="Filter by parent test run ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items = await crud_budget.get_budgets_paginated(db, test_run_id=test_run_id, filters=filters, limit=10000, offset=0)
    buffer = export_service.generate_excel_export(list(items))
    
    filename = f"Budget_Export_{datetime.now().strftime('%Y%m%d')}.xlsx"
    
    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return StreamingResponse(
        buffer, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )

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
    payload: BudgetUpdate,
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

    # Lock guard: prevent any updates on a locked budget
    if budget.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update a locked budget",
        )

    # Extract per-budget overrides from the payload
    overrides = {
        k: v for k, v in payload.model_dump().items()
        if k.endswith("_override") and v is not None
    }
    try:
        calculated = await compute_and_get_budget_fields(
            tc_count=payload.tc_count,
            duration_in_days=payload.duration_in_days,
            db=db,
            overrides=overrides or None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    budget = await crud_budget.update_budget(
        db,
        budget,
        full_budget_data=calculated,
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
