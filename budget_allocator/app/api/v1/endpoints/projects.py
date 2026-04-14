"""
app/api/v1/endpoints/projects.py
---------------------------------
HTTP Controller for Project resources.
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.crud import crud_project
from app.models.models import User
from app.schemas.schemas import (
    PaginatedResponse,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    BudgetSummaryOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


# ===========================================================================
# Projects
# ===========================================================================

@router.get("", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    business_unit: str | None = Query(None, description="Filter by business unit"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ProjectOut]:
    total = await crud_project.count_active_projects(db, business_unit=business_unit)
    items = await crud_project.get_all_projects_paginated(db, business_unit=business_unit, limit=limit, offset=offset)
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
