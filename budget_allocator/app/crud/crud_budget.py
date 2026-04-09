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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget


async def get_budget_by_id(db: AsyncSession, budget_id: uuid.UUID) -> Budget | None:
    """Fetch a single Budget by primary key."""
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    return result.scalar_one_or_none()


async def get_budget_for_subdivision(
    db: AsyncSession,
    sub_division_id: uuid.UUID,
) -> Budget | None:
    """
    Return the Budget associated with a given SubDivision, or None.

    Used to enforce the one-budget-per-subdivision rule before a POST.
    """
    result = await db.execute(
        select(Budget).where(Budget.sub_division_id == sub_division_id)
    )
    return result.scalar_one_or_none()


async def create_budget(
    db: AsyncSession,
    *,
    sub_division_id: uuid.UUID,
    tc_count: int,
    duration_in_days: int,
    calculated_fields: dict,
) -> Budget:
    """
    Persist a fully-calculated Budget row.

    Parameters
    ----------
    sub_division_id:
        FK to the parent SubDivision.
    tc_count / duration_in_days:
        The raw manual inputs provided by the manager.
    calculated_fields:
        A flat ``dict`` of all computed column values returned by
        ``compute_and_get_budget_fields``.  Spread directly onto the model.
    """
    budget = Budget(
        sub_division_id=sub_division_id,
        tc_count=tc_count,
        duration_in_days=duration_in_days,
        **calculated_fields,
    )
    db.add(budget)
    await db.flush()
    await db.refresh(budget)   # Fix #15: reload updated_at / created_at from DB
    return budget


async def update_budget(
    db: AsyncSession,
    budget: Budget,
    *,
    tc_count: int,
    duration_in_days: int,
    calculated_fields: dict,
) -> Budget:
    """
    Overwrite an existing Budget with freshly-calculated values.

    All calculated fields are replaced atomically — partial updates are
    intentionally not supported because any single-field change invalidates
    the formula consistency of the entire row.
    """
    budget.tc_count = tc_count
    budget.duration_in_days = duration_in_days
    for field, value in calculated_fields.items():
        setattr(budget, field, value)
    db.add(budget)
    await db.flush()            # Trigger UPDATE so DB sets updated_at
    await db.refresh(budget)    # Fix #15: reload updated_at before returning
    return budget


async def delete_budget(db: AsyncSession, budget: Budget) -> None:
    """Delete a Budget row."""
    await db.delete(budget)
