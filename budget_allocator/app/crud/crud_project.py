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

from app.models.models import Project, SubDivision
from app.schemas.schemas import ProjectCreate, ProjectUpdate, SubDivisionCreate, SubDivisionUpdate


# ===========================================================================
# Projects
# ===========================================================================


async def get_all_projects(db: AsyncSession) -> Sequence[Project]:
    """Return every project ordered by creation date (oldest first)."""
    result = await db.execute(select(Project).order_by(Project.created_at))
    return result.scalars().all()


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
    stmt = select(Project).where(Project.id == project_id)
    if load_subdivisions:
        stmt = stmt.options(selectinload(Project.sub_divisions))
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
    Delete a Project row (cascades to SubDivisions and their Budgets via
    the ``ondelete="CASCADE"`` FK constraints defined on the models).
    """
    await db.delete(project)


# ===========================================================================
# SubDivisions
# ===========================================================================


async def get_subdivisions_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> Sequence[SubDivision]:
    """Return all sub-divisions belonging to ``project_id``, ordered by name."""
    result = await db.execute(
        select(SubDivision)
        .where(SubDivision.project_id == project_id)
        .order_by(SubDivision.name)
    )
    return result.scalars().all()


async def get_subdivision_by_id(
    db: AsyncSession,
    sd_id: uuid.UUID,
) -> SubDivision | None:
    """Fetch a single SubDivision by primary key."""
    result = await db.execute(select(SubDivision).where(SubDivision.id == sd_id))
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
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(sd, field, value)
    db.add(sd)
    await db.flush()   # Fix #15: flush so updated_at is set by DB
    return sd


async def delete_subdivision(db: AsyncSession, sd: SubDivision) -> None:
    """Delete a SubDivision row (cascades to its Budget)."""
    await db.delete(sd)
