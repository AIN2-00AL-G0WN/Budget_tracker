"""
app/crud/crud_project.py
------------------------
Data-Access Layer (Repository) for the Project and SubDivision entities.

Design rules
~~~~~~~~~~~~
* Every function is async and accepts an ``AsyncSession`` as its first argument.
* Functions return ORM model objects (or None / lists) — they NEVER raise HTTP
  exceptions.  HTTP-layer decisions (404, 409, …) belong in the router.
* ``db.flush()`` is used after inserts so the caller can read back
  database-generated fields (id, created_at …) without committing.
  The surrounding unit-of-work (managed by the ``get_db`` dependency's
  ``async with session.begin()`` block) commits at the end of the request.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Budget, Project, SubDivision
from app.schemas.schemas import ProjectCreate, ProjectUpdate, SubDivisionCreate, SubDivisionUpdate


# ===========================================================================
# Projects
# ===========================================================================


async def get_all_projects(db: AsyncSession) -> Sequence[Project]:
    """Return every non-deleted project ordered by creation date (oldest first)."""
    result = await db.execute(
        select(Project)
        .where(Project.is_deleted == False)  # noqa: E712
        .order_by(Project.created_at)
    )
    return result.scalars().all()


async def get_all_projects_paginated(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Project]:
    """Return a paginated slice of non-deleted projects."""
    result = await db.execute(
        select(Project)
        .where(Project.is_deleted == False)  # noqa: E712
        .order_by(Project.created_at)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def count_active_projects(db: AsyncSession) -> int:
    """Return the total count of non-deleted projects."""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(Project).where(Project.is_deleted == False)  # noqa: E712
    )
    return result.scalar_one()


async def get_project_by_id(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    load_subdivisions: bool = False,
) -> Project | None:
    """
    Fetch a single project by primary key.

    Parameters
    ----------
    load_subdivisions:
        When ``True``, eagerly loads the ``sub_divisions`` relationship via
        ``selectinload`` so the caller can access ``project.sub_divisions``
        without an extra query.
    """
    stmt = select(Project).where(
        Project.id == project_id,
        Project.is_deleted == False,  # noqa: E712
    )
    if load_subdivisions:
        stmt = stmt.options(selectinload(Project.sub_divisions.and_(SubDivision.is_deleted == False)))  # noqa: E712
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_project(db: AsyncSession, payload: ProjectCreate) -> Project:
    """
    Persist a new project row from a validated Pydantic schema.

    Uses ``db.flush()`` to populate ``id`` and ``created_at`` from the DB,
    then ``db.refresh()`` to reload server-generated fields (including the
    accurate ``created_at`` / ``updated_at`` timestamps) before returning.
    """
    project = Project(**payload.model_dump())
    db.add(project)
    await db.flush()
    await db.refresh(project)   # Fix #15: reload updated_at / server defaults
    return project


async def update_project(
    db: AsyncSession,
    project: Project,
    payload: ProjectUpdate,
) -> Project:
    """
    Apply a partial update to an already-fetched Project ORM object.

    Calls ``flush()`` to trigger the SQL UPDATE (which causes the DB to set
    ``updated_at`` via ``onupdate``), then ``refresh()`` to load that new
    timestamp into the in-memory object so the API response is accurate
    (Fix #15).
    """
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(project, field, value)
    db.add(project)
    await db.flush()            # Trigger UPDATE; DB sets updated_at
    await db.refresh(project)   # Fix #15: reload updated_at before returning
    return project


async def delete_project(db: AsyncSession, project: Project) -> None:
    """
    Soft-delete a Project by setting is_deleted=True.

    The row and all child data remains in the database for audit purposes.
    Hard foreign-key cascades are intentionally NOT triggered.
    """
    project.is_deleted = True
    db.add(project)
    await db.flush()


# ===========================================================================
# SubDivisions
# ===========================================================================


async def get_project_summary(
    db: AsyncSession, project_id: uuid.UUID
) -> dict | None:
    """
    Returns aggregated budget stats across all sub-divisions for a given
    project, returning a dictionary representation of BudgetSummaryOut.
    """
    from sqlalchemy import func
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
        ).join(SubDivision, Budget.sub_division_id == SubDivision.id).where(
            SubDivision.project_id == project_id,
            SubDivision.is_deleted == False,  # noqa: E712
            Budget.is_deleted == False,  # noqa: E712
        )
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_subdivisions_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> Sequence[SubDivision]:
    """Return all non-deleted sub-divisions belonging to ``project_id``, ordered by name."""
    result = await db.execute(
        select(SubDivision)
        .where(
            SubDivision.project_id == project_id,
            SubDivision.is_deleted == False,  # noqa: E712
        )
        .order_by(SubDivision.name)
    )
    return result.scalars().all()


async def get_subdivisions_for_project_paginated(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[SubDivision]:
    """Return a paginated slice of non-deleted sub-divisions for a project."""
    result = await db.execute(
        select(SubDivision)
        .where(
            SubDivision.project_id == project_id,
            SubDivision.is_deleted == False,  # noqa: E712
        )
        .order_by(SubDivision.name)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def count_active_subdivisions_for_project(
    db: AsyncSession, project_id: uuid.UUID
) -> int:
    """Return the total count of non-deleted subdivisions for a project."""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(SubDivision).where(
            SubDivision.project_id == project_id,
            SubDivision.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one()


async def get_subdivision_by_id(
    db: AsyncSession,
    sd_id: uuid.UUID,
) -> SubDivision | None:
    """Fetch a single non-deleted SubDivision by primary key."""
    result = await db.execute(
        select(SubDivision).where(
            SubDivision.id == sd_id,
            SubDivision.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_subdivision(
    db: AsyncSession,
    project_id: uuid.UUID,
    payload: SubDivisionCreate,
) -> SubDivision:
    """
    Persist a new SubDivision for the given project.

    ``project_id`` is taken from the URL path parameter rather than the
    request body (Fix #7).
    """
    data = payload.model_dump()
    data["project_id"] = project_id
    sd = SubDivision(**data)
    db.add(sd)
    await db.flush()
    await db.refresh(sd)   # Fix #15: reload server-generated timestamps
    return sd


async def update_subdivision(
    db: AsyncSession,
    sd: SubDivision,
    payload: SubDivisionUpdate,
) -> SubDivision:
    """Apply a partial update to an already-fetched SubDivision ORM object."""
    from app.models.models import SubDivisionStatus, Budget
    from sqlalchemy import select

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(sd, field, value)

    # Auto-lock budget if subdivision is marked COMPLETED
    if getattr(sd, "status", None) == SubDivisionStatus.COMPLETED:
        budget_result = await db.execute(
            select(Budget).where(Budget.sub_division_id == sd.id)
        )
        budget = budget_result.scalar_one_or_none()
        if budget:
            budget.is_locked = True
            db.add(budget)

    db.add(sd)
    await db.flush()            # Trigger UPDATE; DB sets updated_at
    await db.refresh(sd)        # Bug #6 fix: reload updated_at before returning
    return sd


async def delete_subdivision(db: AsyncSession, sd: SubDivision) -> None:
    """Soft-delete a SubDivision by setting is_deleted=True."""
    sd.is_deleted = True
    db.add(sd)
    await db.flush()
