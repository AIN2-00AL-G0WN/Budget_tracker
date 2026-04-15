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
from app.core.context import current_change_reason
from app.core.database import get_db
from app.crud import crud_budget, crud_notification, crud_run, crud_holiday
from app.models.models import User, Run, Team, Family
from sqlalchemy import select
from app.schemas.schemas import (
    BudgetCreate,
    BudgetUpdate,
    BudgetOut,
    BudgetVersionOut,
    BudgetRestore,
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
    run_id: uuid.UUID = Query(..., description="Parent run ID"),
    payload: BudgetCreate = ...,
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
    # 1. Validate Run exists
    tr = await crud_run.get_run_by_id(db, run_id)
    if not tr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # 2. Guard: one budget per run
    existing = await crud_budget.get_budget_for_run(db, run_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A budget already exists for this Run. "
                "Use PATCH /budgets/{id} to update it."
            ),
        )

    # Check for intercepted dates and update TestRun
    if payload.start_date or payload.end_date:
        if payload.start_date:
            tr.start_date = payload.start_date
        if payload.end_date:
            tr.end_date = payload.end_date
        db.add(tr)
        await db.flush()

    # Template Inheritance (Cloning)
    tc_count = payload.tc_count
    
    # Calculate expected duration using the new calendar logic
    stmt = select(Family.business_unit).select_from(Run).join(Team).join(Family).where(Run.id == run_id)
    bu_result = await db.execute(stmt)
    business_unit = bu_result.scalar_one()

    expected_duration = await crud_holiday.calculate_working_days(
        db, payload.start_date, payload.end_date, business_unit
    )

    total_calendar_days = float((payload.end_date - payload.start_date).days + 1)

    # Validate against frontend
    duration = payload.duration_in_days
    # Bounded Range Validation allows for scheduled weekend/holiday work!
    if duration is not None:
        if duration < expected_duration:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duration ({duration}) cannot be less than the standard non-holiday working days ({expected_duration}).",
            )
        if duration > total_calendar_days:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duration ({duration}) cannot exceed the total calendar days ({total_calendar_days}) in the given range.",
            )
    else:
        duration = expected_duration

    overrides = {
        k: v for k, v in payload.model_dump(exclude={"start_date", "end_date"}).items()
        if k.endswith("_override") and v is not None
    }

    if tc_count is None or not overrides:
        previous = await crud_budget.get_previous_budget_for_run(db, run_id)
        if previous:
            if tc_count is None:
                tc_count = previous.tc_count
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

    if tc_count is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tc_count is required for the first budget of a team."
        )

    # 4 & 5. Run calculation engine (raises ValueError on bad rate-card config)
    try:
        calculated = await compute_and_get_budget_fields(
            run_id=run_id,
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
        run_id=run_id,
        full_budget_data=calculated,
    )
    logger.info(
        "Budget created by %s for run=%s: total=%.2f",
        current_user.username,
        run_id,
        budget.total_budget,
    )
    return budget  # type: ignore[return-value]


@router.get("/{budget_id}/history", response_model=list[BudgetVersionOut])
async def get_budget_history(
    budget_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Retrieve the full version history and snapshots for a specific budget."""
    history = await crud_budget.get_budget_history(db, budget_id)
    if not history:
        # Check if budget even exists to return 404
        budget = await crud_budget.get_budget_by_id(db, budget_id)
        if not budget:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return history


@router.post("/{budget_id}/restore", response_model=BudgetOut)
async def restore_budget(
    budget_id: uuid.UUID,
    payload: BudgetRestore,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetOut:
    """
    Restore a budget to a previous historical version.
    This creates a new version entry in the AuditLog.
    """
    budget = await crud_budget.get_budget_by_id(db, budget_id)
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")

    if budget.is_locked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot restore a locked budget")

    # Fetch the history to find the requested snapshot
    history = await crud_budget.get_budget_history(db, budget_id)
    target_snapshot = None
    for version in history:
        if version["edit_timestamp"] == payload.target_timestamp:
            target_snapshot = version["snapshot"]
            break

    if not target_snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"No budget version found at timestamp {payload.target_timestamp}"
        )

    # Set the mandatory change reason for the new AuditLog entry
    current_change_reason.set(payload.change_reason)

    # Extract all values needed for the update from the snapshot dictionary.
    # Exclude system columns that shouldn't be blindly updated via snapshot.
    exclude_keys = {"id", "run_id", "is_deleted", "is_locked", "created_at", "updated_at"}
    full_budget_data = {
        k: v for k, v in target_snapshot.items() if k not in exclude_keys
    }

    # update_budget replaces the values entirely and hits the database, which triggers our AuditLog
    restored_budget = await crud_budget.update_budget(
        db,
        budget,
        full_budget_data=full_budget_data
    )
    
    logger.info(
        "Budget %s restored by %s to timestamp %s",
        budget_id,
        current_user.username,
        payload.target_timestamp,
    )
    return restored_budget  # type: ignore[return-value]


@router.get("/budgets", response_model=PaginatedResponse[BudgetOut])
async def list_budgets(
    run_id: uuid.UUID | None = Query(None, description="Filter by parent run ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BudgetOut]:
    total = await crud_budget.count_active_budgets(db, run_id=run_id, filters=filters)
    items = await crud_budget.get_budgets_paginated(db, run_id=run_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

@router.get("/budgets/export")
async def export_budgets(
    run_id: uuid.UUID | None = Query(None, description="Filter by parent run ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items = await crud_budget.get_budgets_paginated(db, run_id=run_id, filters=filters, limit=10000, offset=0)
    
    export_data = []
    for item in items:
        data_dict = BudgetOut.model_validate(item).model_dump()
        data_dict["run_name"] = item.run.name if item.run else ""
        data_dict["team_name"] = item.run.team.name if item.run and item.run.team else ""
        data_dict["start_date"] = item.run.start_date.isoformat() if getattr(item.run, 'start_date', None) else ""
        data_dict["end_date"] = item.run.end_date.isoformat() if getattr(item.run, 'end_date', None) else ""
        export_data.append(data_dict)
    
    buffer = export_service.generate_excel_export(export_data)
    
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

    current_change_reason.set(payload.change_reason)

    tr = await crud_run.get_run_by_id(db, budget.run_id)
    start_date = payload.start_date or tr.start_date
    end_date = payload.end_date or tr.end_date
    
    if payload.start_date or payload.end_date:
        if payload.start_date:
            tr.start_date = payload.start_date
        if payload.end_date:
            tr.end_date = payload.end_date
        db.add(tr)
        await db.flush()

    # Calculate expected duration using the new calendar logic
    stmt = select(Family.business_unit).select_from(Run).join(Team).join(Family).where(Run.id == budget.run_id)
    bu_result = await db.execute(stmt)
    business_unit = bu_result.scalar_one()

    expected_duration = await crud_holiday.calculate_working_days(
        db, start_date, end_date, business_unit
    )
    total_calendar_days = float((end_date - start_date).days + 1)

    duration = payload.duration_in_days
    if duration is not None:
        if duration < expected_duration:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duration ({duration}) cannot be less than the standard non-holiday working days ({expected_duration}).",
            )
        if duration > total_calendar_days:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duration ({duration}) cannot exceed the total calendar days ({total_calendar_days}) in the given range.",
            )
    else:
        duration = expected_duration

    # Extract per-budget overrides from the payload
    overrides = {
        k: v for k, v in payload.model_dump(exclude={"start_date", "end_date"}).items()
        if k.endswith("_override") and v is not None
    }
    try:
        calculated = await compute_and_get_budget_fields(
            run_id=budget.run_id,
            tc_count=payload.tc_count if payload.tc_count is not None else budget.tc_count,
            duration_in_days=duration,
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
    reason: str = Query(..., min_length=5, description="Mandatory audit explanation"),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    budget = await crud_budget.get_budget_by_id(db, budget_id)
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
        
    current_change_reason.set(reason)
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
