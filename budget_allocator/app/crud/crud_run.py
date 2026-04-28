"""
app/crud/crud_run.py
---------------------
Data-Access Layer (Repository) for the Run entity (formerly TestRun).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.models import Run
from app.schemas.schemas import RunCreate, RunUpdate
from app.api.dependencies.filters import BudgetFilterParams
from app.core.context import current_username


def _apply_budget_filters(stmt, filters: BudgetFilterParams):
    from app.models.models import Budget
    from sqlalchemy import or_, and_

    stmt = stmt.outerjoin(Budget, and_(Budget.run_id == Run.id, Budget.is_deleted == False))  # noqa: E712

    if filters.min_total_cost is not None:
        stmt = stmt.where(Budget.total_budget >= filters.min_total_cost)
    if filters.max_total_cost is not None:
        stmt = stmt.where(Budget.total_budget <= filters.max_total_cost)
    if filters.min_headcount is not None:
        stmt = stmt.where(Budget.manual_hc >= filters.min_headcount)
    if filters.is_locked is not None:
        stmt = stmt.where(Budget.is_locked == filters.is_locked)
    if filters.has_overrides is not None:
        override_cols = [
            Budget.manual_tc_multiplier_override,
            Budget.automation_tc_multiplier_override,
            Budget.adhoc_request_multiplier_override,
            Budget.working_days_per_week_override,
            Budget.hrs_per_wk_per_hc_override,
            Budget.manual_hc_divisor_override,
            Budget.automation_hc_divisor_override,
            Budget.sqpm_boise_pct_override,
            Budget.pl_pct_override,
            Budget.per_wqe_pct_override,
            Budget.asqpm_pct_override,
            Budget.lab_tech_manager_pct_override,
            Budget.project_manager_pct_override,
        ]
        if filters.has_overrides:
            stmt = stmt.where(or_(col.is_not(None) for col in override_cols))
        else:
            stmt = stmt.where(and_(col.is_(None) for col in override_cols))
    return stmt


async def get_runs_paginated(
    db: AsyncSession,
    *,
    team_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Run]:
    """Return a paginated slice of non-deleted runs."""
    stmt = select(Run).where(Run.is_deleted == False)  # noqa: E712
    if team_id:
        stmt = stmt.where(Run.team_id == team_id)
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
    stmt = stmt.options(joinedload(Run.team)).order_by(Run.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_active_runs(
    db: AsyncSession,
    team_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
) -> int:
    """Return the total count of non-deleted runs."""
    stmt = select(func.count()).select_from(Run).where(Run.is_deleted == False)  # noqa: E712
    if team_id:
        stmt = stmt.where(Run.team_id == team_id)
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_run_by_id(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Run | None:
    """Fetch a single non-deleted Run by primary key."""
    result = await db.execute(
        select(Run).where(
            Run.id == run_id,
            Run.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_run(
    db: AsyncSession,
    team_id: uuid.UUID,
    payload: RunCreate,
) -> Run:
    """Persist a new Run."""
    data = payload.model_dump()
    data["team_id"] = team_id
    data["created_by_name"] = current_username.get()
    data["updated_by_name"] = current_username.get()
    run = Run(**data)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def update_run(
    db: AsyncSession,
    run: Run,
    payload: RunUpdate,
) -> Run:
    """Apply a partial update to an already-fetched Run ORM object."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(run, field, value)
    run.updated_by_name = current_username.get()
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def delete_run(db: AsyncSession, run: Run) -> None:
    """Soft-delete a Run."""
    run.is_deleted = True
    run.updated_by_name = current_username.get()
    db.add(run)
    await db.flush()
