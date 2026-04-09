"""
app/api/v1/endpoints/projects.py
---------------------------------
HTTP Controller for Project and SubDivision resources.

This router is intentionally thin:  it validates schemas via Pydantic,
delegates ALL database operations to ``app.crud.crud_project``, raises the
appropriate HTTP exceptions when the CRUD layer signals a missing or conflicting
resource, and serialises the response.

Route summary
~~~~~~~~~~~~~
  GET    /projects                       — list all projects
  POST   /projects                       — create project  [admin]
  GET    /projects/{id}                  — get project detail (with sub-divisions)
  PATCH  /projects/{id}                  — update project  [admin]
  DELETE /projects/{id}                  — delete project  [admin]

  GET    /projects/{id}/subdivisions     — list sub-divisions for a project
  POST   /projects/{id}/subdivisions     — create sub-division  [admin]
  PATCH  /projects/subdivisions/{id}     — update sub-division  [admin]
  DELETE /projects/subdivisions/{id}     — delete sub-division  [admin]
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.crud import crud_project
from app.models.models import User
from app.schemas.schemas import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SubDivisionCreate,
    SubDivisionOut,
    SubDivisionUpdate,
)
from sqlalchemy.ext.asyncio import AsyncSession

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
    return await crud_project.get_all_projects(db)  # type: ignore[return-value]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await crud_project.create_project(db, payload)
    logger.info("Project created: %s (%s)", project.name, project.id)
    return project  # type: ignore[return-value]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    project = await crud_project.get_project_by_id(db, project_id, load_subdivisions=True)
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
    project = await crud_project.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project = await crud_project.update_project(db, project, payload)
    return project  # type: ignore[return-value]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await crud_project.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await crud_project.delete_project(db, project)


# ===========================================================================
# SubDivisions (nested under Projects)
# ===========================================================================


@router.get("/{project_id}/subdivisions", response_model=list[SubDivisionOut])
async def list_subdivisions(
    project_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SubDivisionOut]:
    return await crud_project.get_subdivisions_for_project(db, project_id)  # type: ignore[return-value]


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
    # Validate that the parent project exists before creating a child
    parent = await crud_project.get_project_by_id(db, project_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    sd = await crud_project.create_subdivision(db, project_id, payload)
    return sd  # type: ignore[return-value]


@router.patch("/subdivisions/{sd_id}", response_model=SubDivisionOut)
async def update_subdivision(
    sd_id: uuid.UUID,
    payload: SubDivisionUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> SubDivisionOut:
    sd = await crud_project.get_subdivision_by_id(db, sd_id)
    if not sd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SubDivision not found")
    sd = await crud_project.update_subdivision(db, sd, payload)
    return sd  # type: ignore[return-value]


@router.delete("/subdivisions/{sd_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subdivision(
    sd_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    sd = await crud_project.get_subdivision_by_id(db, sd_id)
    if not sd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SubDivision not found")
    await crud_project.delete_subdivision(db, sd)
