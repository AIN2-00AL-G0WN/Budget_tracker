"""
app/crud/crud_family.py
-----------------------
Data-Access Layer for Family (formerly Project) and Team (formerly SubDivision) entities.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Budget, Family, Team
from app.schemas.schemas import FamilyCreate, FamilyUpdate, TeamCreate, TeamUpdate
from app.api.dependencies.filters import WorkflowFilterParams
from app.core.context import current_username


# ===========================================================================
# Families
# ===========================================================================


async def get_all_families(
    db: AsyncSession,
    *,
    load_teams: bool = False,
) -> Sequence[Family]:
    """Return every non-deleted family ordered by creation date."""
    stmt = select(Family).where(Family.is_deleted == False)  # noqa: E712
    if load_teams:
        stmt = stmt.options(selectinload(Family.teams.and_(Team.is_deleted == False)))
    stmt = stmt.order_by(Family.created_at)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_all_families_paginated(
    db: AsyncSession,
    *,
    business_unit_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    load_teams: bool = False,
) -> Sequence[Family]:
    """Return a paginated slice of non-deleted families."""
    stmt = select(Family).where(Family.is_deleted == False)  # noqa: E712
    if business_unit_id:
        stmt = stmt.where(Family.business_unit_id == business_unit_id)
    if load_teams:
        stmt = stmt.options(selectinload(Family.teams.and_(Team.is_deleted == False)))
    stmt = stmt.order_by(Family.created_at).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_active_families(db: AsyncSession, business_unit_id: uuid.UUID | None = None) -> int:
    """Return the total count of non-deleted families."""
    stmt = select(func.count()).select_from(Family).where(Family.is_deleted == False)  # noqa: E712
    if business_unit_id:
        stmt = stmt.where(Family.business_unit_id == business_unit_id)
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_family_by_id(
    db: AsyncSession,
    family_id: uuid.UUID,
    *,
    load_teams: bool = False,
) -> Family | None:
    """Fetch a single non-deleted family by primary key."""
    stmt = select(Family).where(
        Family.id == family_id,
        Family.is_deleted == False,  # noqa: E712
    )
    if load_teams:
        stmt = stmt.options(selectinload(Family.teams.and_(Team.is_deleted == False)))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_family(db: AsyncSession, payload: FamilyCreate) -> Family:
    """Persist a new Family row."""
    data = payload.model_dump()
    data["created_by_name"] = current_username.get()
    data["updated_by_name"] = current_username.get()
    family = Family(**data)
    db.add(family)
    await db.flush()
    await db.refresh(family)
    return family


async def update_family(
    db: AsyncSession,
    family: Family,
    payload: FamilyUpdate,
) -> Family:
    """Apply a partial update to an already-fetched Family ORM object."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(family, field, value)
    family.updated_by_name = current_username.get()
    db.add(family)
    await db.flush()
    await db.refresh(family)
    return family


async def delete_family(db: AsyncSession, family: Family) -> None:
    """Soft-delete a Family."""
    family.is_deleted = True
    family.updated_by_name = current_username.get()
    db.add(family)
    await db.flush()


async def get_family_budget_summary(
    db: AsyncSession, family_id: uuid.UUID
) -> dict | None:
    """Aggregated budget stats across all teams for a given family."""
    from app.models.models import Run
    result = await db.execute(
        select(
            func.coalesce(func.sum(Budget.tc_count), 0).label("tc_count"),
            func.coalesce(func.sum(Budget.duration_wks), 0).label("duration_wks"),
            func.coalesce(func.sum(Budget.manual_hc), 0).label("manual_hc"),
            func.coalesce(func.sum(Budget.automation_hc), 0).label("automation_hc"),
            func.coalesce(func.sum(Budget.manual_hc_cost), 0).label("manual_hc_cost"),
            func.coalesce(func.sum(Budget.automation_hc_cost), 0).label("automation_hc_cost"),
            func.coalesce(func.sum(Budget.lead_cost), 0).label("lead_cost"),
            func.coalesce(func.sum(Budget.sqpm_cost_boise), 0).label("sqpm_cost_boise"),
            func.coalesce(func.sum(Budget.pl_cost), 0).label("pl_cost"),
            func.coalesce(func.sum(Budget.per_wqe_cost), 0).label("per_wqe_cost"),
            func.coalesce(func.sum(Budget.asqpm_cost), 0).label("asqpm_cost"),
            func.coalesce(func.sum(Budget.lab_tech_manager_cost), 0).label("lab_tech_manager_cost"),
            func.coalesce(func.sum(Budget.project_manager_cost), 0).label("project_manager_cost"),
            func.coalesce(func.sum(Budget.total_budget), 0).label("total_budget"),
        ).join(Run, Budget.run_id == Run.id).join(Team, Run.team_id == Team.id).where(
            Team.family_id == family_id,
            Team.is_deleted == False,  # noqa: E712
            Run.is_deleted == False,  # noqa: E712
            Budget.is_deleted == False,  # noqa: E712
        )
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


# ===========================================================================
# Teams (formerly SubDivisions)
# ===========================================================================


def _apply_workflow_filters(stmt, filters: WorkflowFilterParams):
    if filters.status:
        stmt = stmt.where(Team.status == filters.status)
    if filters.business_unit_id:
        stmt = stmt.join(Family, Family.id == Team.family_id)
        stmt = stmt.where(Family.business_unit_id == filters.business_unit_id)
    return stmt


async def get_teams_paginated(
    db: AsyncSession,
    *,
    family_id: uuid.UUID | None = None,
    filters: WorkflowFilterParams | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Team]:
    """Return a paginated slice of non-deleted teams."""
    stmt = select(Team).where(Team.is_deleted == False)  # noqa: E712
    if family_id:
        stmt = stmt.where(Team.family_id == family_id)
    if filters:
        stmt = _apply_workflow_filters(stmt, filters)
    stmt = stmt.order_by(Team.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_active_teams(
    db: AsyncSession,
    family_id: uuid.UUID | None = None,
    filters: WorkflowFilterParams | None = None,
) -> int:
    """Return the total count of non-deleted teams."""
    stmt = select(func.count()).select_from(Team).where(Team.is_deleted == False)  # noqa: E712
    if family_id:
        stmt = stmt.where(Team.family_id == family_id)
    if filters:
        stmt = _apply_workflow_filters(stmt, filters)
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_team_by_id(
    db: AsyncSession,
    team_id: uuid.UUID,
) -> Team | None:
    """Fetch a single non-deleted Team by primary key."""
    result = await db.execute(
        select(Team).where(
            Team.id == team_id,
            Team.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_team(
    db: AsyncSession,
    family_id: uuid.UUID,
    payload: TeamCreate,
) -> Team:
    """Persist a new Team. family_id comes from the URL path parameter."""
    data = payload.model_dump()
    data["family_id"] = family_id
    data["created_by_name"] = current_username.get()
    data["updated_by_name"] = current_username.get()
    team = Team(**data)
    db.add(team)
    await db.flush()
    await db.refresh(team)
    return team


async def update_team(
    db: AsyncSession,
    team: Team,
    payload: TeamUpdate,
) -> Team:
    """Apply a partial update to an already-fetched Team ORM object."""
    from app.models.models import TeamStatus, Run

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(team, field, value)

    # Auto-lock budget if team is marked COMPLETED
    if getattr(team, "status", None) == TeamStatus.COMPLETED:
        run_result = await db.execute(
            select(Run).where(Run.team_id == team.id)
        )
        for run in run_result.scalars().all():
            if run.budget:
                run.budget.is_locked = True
                db.add(run.budget)

    team.updated_by_name = current_username.get()
    db.add(team)
    await db.flush()
    await db.refresh(team)
    return team


async def delete_team(db: AsyncSession, team: Team) -> None:
    """Soft-delete a Team."""
    team.is_deleted = True
    team.updated_by_name = current_username.get()
    db.add(team)
    await db.flush()
