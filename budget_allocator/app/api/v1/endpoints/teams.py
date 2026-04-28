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
from app.crud import crud_family
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
    family_id: uuid.UUID | None = Query(None, description="Filter by parent family ID"),
    filters: WorkflowFilterParams = Depends(get_workflow_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TeamOut]:
    total = await crud_family.count_active_teams(db, family_id=family_id, filters=filters)
    items = await crud_family.get_teams_paginated(db, family_id=family_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    family_id: uuid.UUID = Query(..., description="Parent family ID"),
    payload: TeamCreate = ...,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> TeamOut:
    parent = await crud_family.get_family_by_id(db, family_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family not found")

    team = await crud_family.create_team(db, family_id, payload)
    return team


@router.get("/{team_id}", response_model=TeamOut)
async def get_team(
    team_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TeamOut:
    team = await crud_family.get_team_by_id(db, team_id)
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
    team = await crud_family.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    team = await crud_family.update_team(db, team, payload)
    return team


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    team = await crud_family.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    from app.crud import crud_test_run
    active_runs = await crud_test_run.count_active_test_runs(db, sub_division_id=team_id)
    if active_runs > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete team: {active_runs} run(s) are still present."
        )

    await crud_family.delete_team(db, team)
    return Response(status_code=204)
