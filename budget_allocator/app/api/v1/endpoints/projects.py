"""
app/api/v1/endpoints/projects.py
---------------------------------
Project and SubDivision CRUD endpoints.

  GET    /projects                       — list all projects
  POST   /projects                       — create project
  GET    /projects/{id}                  — get project detail (with sub-divisions)
  PATCH  /projects/{id}                  — update project
  DELETE /projects/{id}                  — delete project

  GET    /projects/{id}/subdivisions     — list sub-divisions for a project
  POST   /projects/{id}/subdivisions     — create sub-division
  PATCH  /subdivisions/{id}              — update sub-division
  DELETE /subdivisions/{id}              — delete sub-division
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.models.models import Project, SubDivision, User
from app.schemas.schemas import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SubDivisionCreate,
    SubDivisionOut,
    SubDivisionUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


# ===========================================================================
# Projects
# ===========================================================================

@router.get("", response_model=list[ProjectOut])
async def list_projects(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    result = await db.execute(select(Project).order_by(Project.created_at))
    return result.scalars().all()  # type: ignore[return-value]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = Project(**payload.model_dump())
    db.add(project)
    await db.flush()
    logger.info("Project created: %s (%s)", project.name, project.id)
    return project  # type: ignore[return-value]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.sub_divisions))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project  # type: ignore[return-value]


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(project, field, val)
    db.add(project)
    return project  # type: ignore[return-value]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await db.delete(project)


# ===========================================================================
# SubDivisions (nested under Projects)
# ===========================================================================

@router.get("/{project_id}/subdivisions", response_model=list[SubDivisionOut])
async def list_subdivisions(
    project_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SubDivisionOut]:
    result = await db.execute(
        select(SubDivision)
        .where(SubDivision.project_id == project_id)
        .order_by(SubDivision.name)
    )
    return result.scalars().all()  # type: ignore[return-value]


@router.post(
    "/{project_id}/subdivisions",
    response_model=SubDivisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_subdivision(
    project_id: uuid.UUID,
    payload: SubDivisionCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> SubDivisionOut:
    # Ensure the parent project exists
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    if not proj_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    data = payload.model_dump()
    data["project_id"] = project_id    # Use path param, not body field
    sd = SubDivision(**data)
    db.add(sd)
    await db.flush()
    return sd  # type: ignore[return-value]


@router.patch("/subdivisions/{sd_id}", response_model=SubDivisionOut)
async def update_subdivision(
    sd_id: uuid.UUID,
    payload: SubDivisionUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> SubDivisionOut:
    result = await db.execute(select(SubDivision).where(SubDivision.id == sd_id))
    sd = result.scalar_one_or_none()
    if not sd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SubDivision not found")

    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(sd, field, val)
    db.add(sd)
    return sd  # type: ignore[return-value]


@router.delete("/subdivisions/{sd_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subdivision(
    sd_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(SubDivision).where(SubDivision.id == sd_id))
    sd = result.scalar_one_or_none()
    if not sd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SubDivision not found")
    await db.delete(sd)
