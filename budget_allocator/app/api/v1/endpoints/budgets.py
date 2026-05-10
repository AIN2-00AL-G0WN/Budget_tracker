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

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.context import current_change_reason
from app.core.database import get_db
from app.crud import crud_budget, crud_notification, crud_run
from app.models.models import User
from app.schemas.schemas import (
    BudgetCreate,
    BudgetUpdate,
    BudgetOut,
    BudgetVersionOut,
    BudgetRestore,
    BudgetTemplateOut,
    NotificationMarkRead,
    NotificationOut,
    PaginatedResponse,
)
from app.api.dependencies.filters import BudgetFilterParams, get_budget_filters
from app.services.calculation_service import compute_and_get_budget_fields, fetch_rate_cards
from app.services import calendar_service, export_service

router = APIRouter(tags=["budgets"])
logger = logging.getLogger(__name__)


# ===========================================================================
# Budgets
# ===========================================================================


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    run_id: uuid.UUID = Query(..., description="Parent run ID"),
    payload: BudgetCreate = ...,
    current_user: User = Depends(get_current_admin_user),
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

    # ── Service: resolve & validate duration ─────────────────────────────────
    try:
        duration_result = await calendar_service.resolve_budget_duration(
            start_date=payload.start_date,
            end_date=payload.end_date,
            requested_duration=payload.duration_in_days,
            run_id=run_id,
            db=db,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    duration = duration_result.duration_in_days

    overrides = {
        k: v for k, v in payload.model_dump(exclude={"start_date", "end_date"}).items()
        if k.endswith("_override") and v is not None
    }

    if payload.tc_count is None or not overrides:
        previous = await crud_budget.get_previous_budget_for_run(db, run_id)
        if previous:
            if payload.tc_count is None:
                payload.tc_count = previous.tc_count
            for k in [
                "manual_tc_multiplier_override", "automation_tc_multiplier_override",
                "adhoc_request_multiplier_override", "working_days_per_week_override",
                "hrs_per_wk_per_hc_override", "manual_hc_divisor_override",
                "automation_hc_divisor_override",
                "manual_hourly_rate_override", "automation_hourly_rate_override",
                "asqpm_hourly_rate_override", "lead_hourly_rate_override", "pm_hourly_rate_override",
                "sqpm_boise_hourly_rate_override", "pl_hourly_rate_override",
                "per_wqe_hourly_rate_override", "lab_tech_manager_hourly_rate_override",
                "sqpm_boise_pct_override", "pl_pct_override", "per_wqe_pct_override",
                "asqpm_pct_override", "lab_tech_manager_pct_override", "project_manager_pct_override"
            ]:
                if overrides.get(k) is None:
                    prev_val = getattr(previous, k, None)
                    if prev_val is not None:
                        overrides[k] = prev_val

    if payload.tc_count is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tc_count is required for the first budget of a team."
        )

    # 4 & 5. Run calculation engine (raises ValueError on bad rate-card config)
    try:
        calculated = await compute_and_get_budget_fields(
            run_id=run_id,
            tc_count=payload.tc_count,
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


@router.get("/budgets/template", response_model=BudgetTemplateOut)
async def get_budget_template(
    run_id: uuid.UUID = Query(..., description="Run ID to get template for"),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetTemplateOut:
    """
    Get a 'template' for creating a new budget.
    If a previous budget exists for the team, it returns those values.
    Otherwise, it returns the global default RateCard values.
    """
    previous = await crud_budget.get_previous_budget_for_run(db, run_id)
    if previous:
        return BudgetTemplateOut(
            tc_count=previous.tc_count,
            manual_tc_multiplier_override=previous.manual_tc_multiplier_override,
            automation_tc_multiplier_override=previous.automation_tc_multiplier_override,
            adhoc_request_multiplier_override=previous.adhoc_request_multiplier_override,
            working_days_per_week_override=previous.working_days_per_week_override,
            hrs_per_wk_per_hc_override=previous.hrs_per_wk_per_hc_override,
            manual_hc_divisor_override=previous.manual_hc_divisor_override,
            automation_hc_divisor_override=previous.automation_hc_divisor_override,
            manual_hourly_rate_override=previous.manual_hourly_rate_override,
            automation_hourly_rate_override=previous.automation_hourly_rate_override,
            asqpm_hourly_rate_override=previous.asqpm_hourly_rate_override,
            lead_hourly_rate_override=previous.lead_hourly_rate_override,
            pm_hourly_rate_override=previous.pm_hourly_rate_override,
            sqpm_boise_hourly_rate_override=previous.sqpm_boise_hourly_rate_override,
            pl_hourly_rate_override=previous.pl_hourly_rate_override,
            per_wqe_hourly_rate_override=previous.per_wqe_hourly_rate_override,
            lab_tech_manager_hourly_rate_override=previous.lab_tech_manager_hourly_rate_override,
            sqpm_boise_pct_override=previous.sqpm_boise_pct_override,
            pl_pct_override=previous.pl_pct_override,
            per_wqe_pct_override=previous.per_wqe_pct_override,
            asqpm_pct_override=previous.asqpm_pct_override,
            lab_tech_manager_pct_override=previous.lab_tech_manager_pct_override,
            project_manager_pct_override=previous.project_manager_pct_override,
        )

    # Fallback to global rate card defaults
    rates = await fetch_rate_cards(db)
    return BudgetTemplateOut(
        tc_count=None,
        manual_tc_multiplier_override=rates.get("manual_tc_multiplier"),
        automation_tc_multiplier_override=rates.get("automation_tc_multiplier"),
        adhoc_request_multiplier_override=rates.get("adhoc_request_multiplier"),
        working_days_per_week_override=rates.get("working_days_per_week"),
        hrs_per_wk_per_hc_override=rates.get("hrs_per_wk_per_hc"),
        manual_hc_divisor_override=rates.get("manual_hc_divisor"),
        automation_hc_divisor_override=rates.get("automation_hc_divisor"),
        manual_hourly_rate_override=rates.get("manual_hc_rate"),
        automation_hourly_rate_override=rates.get("automation_hc_rate"),
        asqpm_hourly_rate_override=rates.get("asqpm_rate"),
        lead_hourly_rate_override=rates.get("lead_rate"),
        pm_hourly_rate_override=rates.get("project_manager_rate"),
        sqpm_boise_hourly_rate_override=rates.get("sqpm_boise_rate"),
        pl_hourly_rate_override=rates.get("pl_rate"),
        per_wqe_hourly_rate_override=rates.get("per_wqe_rate"),
        lab_tech_manager_hourly_rate_override=rates.get("lab_tech_manager_rate"),
        sqpm_boise_pct_override=rates.get("sqpm_boise_pct"),
        pl_pct_override=rates.get("pl_pct"),
        per_wqe_pct_override=rates.get("per_wqe_pct"),
        asqpm_pct_override=rates.get("asqpm_pct"),
        lab_tech_manager_pct_override=rates.get("lab_tech_manager_pct"),
        project_manager_pct_override=rates.get("project_manager_pct"),
    )


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

    # ── Service: resolve & validate duration ─────────────────────────────────
    # Fetch the parent Run so we can fall back to its dates if the payload omits them.
    run_obj = await crud_run.get_run_by_id(db, budget.run_id)
    start_date = payload.start_date or (run_obj.start_date if run_obj else None)
    end_date   = payload.end_date   or (run_obj.end_date   if run_obj else None)

    if start_date is None or end_date is None:
        # No dates available anywhere — skip date-based duration resolution
        # and fall back to the existing stored duration_in_days on the budget.
        if payload.duration_in_days is not None:
            duration = payload.duration_in_days
        elif budget.duration_in_days is not None:
            duration = budget.duration_in_days
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Cannot determine duration: no start_date/end_date found on the "
                    "payload or the Run, and no existing duration_in_days on the budget. "
                    "Please provide start_date and end_date."
                ),
            )
    else:
        try:
            duration_result = await calendar_service.resolve_budget_duration(
                start_date=start_date,
                end_date=end_date,
                requested_duration=payload.duration_in_days,
                run_id=budget.run_id,
                db=db,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
        duration = duration_result.duration_in_days


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
