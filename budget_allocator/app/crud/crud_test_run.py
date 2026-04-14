"""
app/crud/crud_test_run.py
-------------------------
Data-Access Layer (Repository) for the TestRun entity.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.models.models import TestRun
from app.schemas.schemas import TestRunCreate, TestRunUpdate
from app.api.dependencies.filters import BudgetFilterParams


def _apply_budget_filters(stmt, filters: BudgetFilterParams):
    from app.models.models import Budget
    from sqlalchemy import or_, and_
    
    # We join Budget to apply these filters on the TestRun queries
    stmt = stmt.join(Budget, Budget.test_run_id == TestRun.id).where(Budget.is_deleted == False)
    
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
            Budget.hc_rate_card_override,
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

async def get_test_runs_paginated(
    db: AsyncSession,
    *,
    sub_division_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[TestRun]:
    """Return a paginated slice of non-deleted test runs."""
    stmt = select(TestRun).where(TestRun.is_deleted == False)
    if sub_division_id:
        stmt = stmt.where(TestRun.sub_division_id == sub_division_id)
        
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
        
    stmt = stmt.options(joinedload(TestRun.sub_division)).order_by(TestRun.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_active_test_runs(
    db: AsyncSession,
    sub_division_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
) -> int:
    """Return the total count of non-deleted test runs."""
    from sqlalchemy import func
    stmt = select(func.count()).select_from(TestRun).where(TestRun.is_deleted == False)
    if sub_division_id:
        stmt = stmt.where(TestRun.sub_division_id == sub_division_id)
        
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
        
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_test_run_by_id(
    db: AsyncSession,
    test_run_id: uuid.UUID,
) -> TestRun | None:
    """Fetch a single non-deleted TestRun by primary key."""
    result = await db.execute(
        select(TestRun).where(
            TestRun.id == test_run_id,
            TestRun.is_deleted == False,
        )
    )
    return result.scalar_one_or_none()


async def create_test_run(
    db: AsyncSession,
    sub_division_id: uuid.UUID,
    payload: TestRunCreate,
) -> TestRun:
    """Persist a new TestRun."""
    data = payload.model_dump()
    data["sub_division_id"] = sub_division_id
    tr = TestRun(**data)
    db.add(tr)
    await db.flush()
    await db.refresh(tr)
    return tr


async def update_test_run(
    db: AsyncSession,
    tr: TestRun,
    payload: TestRunUpdate,
) -> TestRun:
    """Apply a partial update to an already-fetched TestRun ORM object."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(tr, field, value)

    db.add(tr)
    await db.flush()
    await db.refresh(tr)
    return tr


async def delete_test_run(db: AsyncSession, tr: TestRun) -> None:
    """Soft-delete a TestRun."""
    tr.is_deleted = True
    db.add(tr)
    await db.flush()
