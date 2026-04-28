"""
app/crud/crud_budget.py
-----------------------
Data-Access Layer (Repository) for the Budget entity.

Responsibilities
~~~~~~~~~~~~~~~~
* Owns every ``select`` / ``add`` / ``delete`` query against the ``budgets``
  table.
* Does NOT call ``compute_and_get_budget_fields`` — that belongs in the router
  (or a dedicated orchestration layer) because it couples two service concerns.
* Returns ORM objects or None so the router decides all HTTP status codes.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from app.models.models import Budget, Run, Team
from app.api.dependencies.filters import BudgetFilterParams
from app.core.context import current_username


def _apply_budget_filters(stmt, filters: BudgetFilterParams):
    from sqlalchemy import or_, and_
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


async def get_budgets_paginated(
    db: AsyncSession,
    *,
    run_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Budget]:
    stmt = select(Budget).where(Budget.is_deleted == False)  # noqa: E712
    if run_id:
        stmt = stmt.where(Budget.run_id == run_id)
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
    stmt = stmt.options(joinedload(Budget.run).joinedload(Run.team)).order_by(Budget.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_active_budgets(
    db: AsyncSession,
    run_id: uuid.UUID | None = None,
    filters: BudgetFilterParams | None = None,
) -> int:
    from sqlalchemy import func
    stmt = select(func.count()).select_from(Budget).where(Budget.is_deleted == False)  # noqa: E712
    if run_id:
        stmt = stmt.where(Budget.run_id == run_id)
    if filters:
        stmt = _apply_budget_filters(stmt, filters)
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_budget_by_id(db: AsyncSession, budget_id: uuid.UUID) -> Budget | None:
    """Fetch a single non-deleted Budget by primary key."""
    result = await db.execute(
        select(Budget).where(
            Budget.id == budget_id,
            Budget.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def get_budget_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Budget | None:
    """
    Return the Budget associated with a given Run, or None.
    Used to enforce the one-budget-per-run rule before a POST.

    NOTE: We intentionally do NOT filter by is_deleted here because the
    database enforces an absolute unique index on run_id (not a partial
    index). If a soft-deleted budget exists, we must detect it so the
    endpoint can return a clear 409 instead of a raw IntegrityError.
    """
    result = await db.execute(
        select(Budget).where(
            Budget.run_id == run_id,
        )
    )
    return result.scalar_one_or_none()


# Backward-compat alias
get_budget_for_test_run = get_budget_for_run


async def get_previous_budget_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Budget | None:
    """
    Find the most recent previous Budget belonging to the same Team (joined through Run).
    """
    from app.models.models import Run

    tr = await db.execute(select(Run).where(Run.id == run_id))
    tr_obj = tr.scalar_one_or_none()
    if not tr_obj:
        return None

    result = await db.execute(
        select(Budget)
        .join(Run, Budget.run_id == Run.id)
        .where(
            Run.team_id == tr_obj.team_id,
            Run.id != run_id,
            Budget.is_deleted == False,  # noqa: E712
        )
        .order_by(Budget.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# Backward-compat alias
get_previous_budget_for_test_run = get_previous_budget_for_run


async def create_budget(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    full_budget_data: dict,
) -> Budget:
    """
    Persist a fully-calculated Budget row.
    run_id: FK to the parent Run.
    """
    budget = Budget(
        run_id=run_id,
        created_by_name=current_username.get(),
        updated_by_name=current_username.get(),
        **full_budget_data,
    )
    db.add(budget)
    await db.flush()
    await db.refresh(budget)
    return budget


async def update_budget(
    db: AsyncSession,
    budget: Budget,
    *,
    full_budget_data: dict,
) -> Budget:
    """
    Overwrite an existing Budget with freshly-calculated values.

    All calculated fields are replaced atomically.
    override_fields are also fully replaced.
    """
    _OVERRIDE_KEYS = [
        "manual_tc_multiplier_override", "automation_tc_multiplier_override",
        "adhoc_request_multiplier_override", "working_days_per_week_override",
        "hrs_per_wk_per_hc_override", "manual_hc_divisor_override",
        "automation_hc_divisor_override", 
        "manual_hourly_rate_override", "automation_hourly_rate_override",
        "asqpm_hourly_rate_override", "lead_hourly_rate_override",
        "pm_hourly_rate_override",
        "sqpm_boise_pct_override", "pl_pct_override", "per_wqe_pct_override",
        "asqpm_pct_override", "lab_tech_manager_pct_override", "project_manager_pct_override",
    ]
    for key in _OVERRIDE_KEYS:
        setattr(budget, key, None)   # reset to "use global rate"
        
    for field, value in full_budget_data.items():
        setattr(budget, field, value)
    budget.updated_by_name = current_username.get()
    db.add(budget)
    await db.flush()            # Trigger UPDATE so DB sets updated_at
    await db.refresh(budget)    # Fix #15: reload updated_at before returning
    return budget


async def delete_budget(db: AsyncSession, budget: Budget) -> None:
    """Soft-delete a Budget row by setting is_deleted=True."""
    budget.is_deleted = True
    budget.updated_by_name = current_username.get()
    db.add(budget)
    await db.flush()


async def get_budget_history(db: AsyncSession, budget_id: uuid.UUID) -> Sequence[dict]:
    """Retrieve all historical snapshots for a given budget from the AuditLog."""
    from app.models.models import AuditLog, User
    from sqlalchemy.orm import aliased

    # Join with User to get username
    user_alias = aliased(User)
    
    stmt = (
        select(AuditLog.timestamp, AuditLog.change_reason, AuditLog.new_value, user_alias.username, AuditLog.user_id)
        .outerjoin(user_alias, AuditLog.user_id == user_alias.id)
        .where(
            AuditLog.entity_type == "Budget",
            AuditLog.entity_id == str(budget_id)
        )
        .order_by(AuditLog.timestamp.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    history = []
    for row in rows:
        # Some old audits might not have new_value populated (e.g. DELETE)
        if not row.new_value:
            continue
        history.append({
            "budget_id": str(budget_id),
            "edit_timestamp": row.timestamp,
            "user_id": str(row.user_id) if row.user_id else None,
            "change_reason": row.change_reason,
            "snapshot": row.new_value,
        })
    return history
