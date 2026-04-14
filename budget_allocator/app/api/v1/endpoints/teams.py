"""
app/api/v1/endpoints/teams.py
-----------------------------
HTTP Controller for Team (SubDivision) resources.
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
    TeamCreate,
    TeamOut,
    TeamUpdate,
)
from app.api.dependencies.filters import WorkflowFilterParams, get_workflow_filters

router = APIRouter(prefix="/teams", tags=["teams"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedResponse[TeamOut])
async def list_teams(
    project_id: uuid.UUID | None = Query(None, description="Filter by parent project ID"),
    filters: WorkflowFilterParams = Depends(get_workflow_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TeamOut]:
    total = await crud_project.count_active_teams(db, project_id=project_id, filters=filters)
    items = await crud_project.get_teams_paginated(db, project_id=project_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    project_id: uuid.UUID = Query(..., description="Parent project ID"),
    payload: TeamCreate = ...,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> TeamOut:
    parent = await crud_project.get_project_by_id(db, project_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    team = await crud_project.create_team(db, project_id, payload)
    return team


@router.get("/{team_id}", response_model=TeamOut)
async def get_team(
    team_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TeamOut:
    team = await crud_project.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return team


@router.patch("/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> TeamOut:
    team = await crud_project.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    team = await crud_project.update_team(db, team, payload)
    return team


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    team = await crud_project.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    await crud_project.delete_team(db, team)
    return Response(status_code=204)
