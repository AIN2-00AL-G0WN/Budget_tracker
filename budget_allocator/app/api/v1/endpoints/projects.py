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

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.crud import crud_project
from app.models.models import Budget, Project, SubDivision, User
from app.schemas.schemas import (
    PaginatedResponse,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SubDivisionCreate,
    SubDivisionOut,
    SubDivisionUpdate,
    BudgetSummaryOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


# ===========================================================================
# Projects
# ===========================================================================


@router.get("", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ProjectOut]:
    # Count total non-deleted projects
    total = await crud_project.count_active_projects(db)

    # Fetch paginated slice
    items = await crud_project.get_all_projects_paginated(db, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


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
) -> Response:
    project = await crud_project.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await crud_project.delete_project(db, project)
    return Response(status_code=204)


# ===========================================================================
# SubDivisions (nested under Projects)
# ===========================================================================


@router.get("/{project_id}/subdivisions", response_model=PaginatedResponse[SubDivisionOut])
async def list_subdivisions(
    project_id: uuid.UUID,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SubDivisionOut]:
    # Count total non-deleted subdivisions for this project
    total = await crud_project.count_active_subdivisions_for_project(db, project_id)

    items = await crud_project.get_subdivisions_for_project_paginated(
        db, project_id, limit=limit, offset=offset
    )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


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
) -> Response:
    sd = await crud_project.get_subdivision_by_id(db, sd_id)
    if not sd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SubDivision not found")
    await crud_project.delete_subdivision(db, sd)
    return Response(status_code=204)


# ===========================================================================
# Project Summary / Analytics
# ===========================================================================


@router.get("/{project_id}/summary", tags=["projects"], response_model=BudgetSummaryOut)
async def get_project_summary(
    project_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Aggregate budget figures across all non-deleted sub-divisions for a project.

    Returns totals for `total_budget`, `manual_hc_cost`, and `automation_hc_cost`.
    """
    # Verify project exists
    project = await crud_project.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    summary_data = await crud_project.get_project_summary(db, project_id)
    if not summary_data:
        # If no summary exists, return an empty one
        return BudgetSummaryOut(
            tc_count=0,
            duration_wks=0.0,
            manual_hc=0,
            automation_hc=0,
            manual_hc_cost=0.0,
            automation_hc_cost=0.0,
            lead_cost=0.0,
            sqpm_cost_boise=0.0,
            pl_cost=0.0,
            per_wqe_cost=0.0,
            asqpm_cost=0.0,
            lab_tech_manager_cost=0.0,
            project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)
