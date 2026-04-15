"""
app/api/v1/endpoints/runs.py
------------------------------
HTTP Controller for Run resources (formerly test_runs.py).
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.context import current_change_reason
from app.core.database import get_db
from app.crud import crud_family, crud_run
from app.models.models import User
from app.schemas.schemas import (
    PaginatedResponse,
    RunCreate,
    RunOut,
    RunUpdate,
)
from app.api.dependencies.filters import BudgetFilterParams, get_budget_filters

router = APIRouter(prefix="/runs", tags=["runs"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedResponse[RunOut])
async def list_runs(
    team_id: uuid.UUID | None = Query(None, description="Filter by parent team ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[RunOut]:
    total = await crud_run.count_active_runs(db, team_id=team_id, filters=filters)
    items = await crud_run.get_runs_paginated(db, team_id=team_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    team_id: uuid.UUID = Query(..., description="Parent team ID"),
    payload: RunCreate = ...,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    parent = await crud_family.get_team_by_id(db, team_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    run = await crud_run.create_run(db, team_id, payload)
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    run = await crud_run.get_run_by_id(db, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.patch("/{run_id}", response_model=RunOut)
async def update_run(
    run_id: uuid.UUID,
    payload: RunUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    run = await crud_run.get_run_by_id(db, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    current_change_reason.set(payload.change_reason)
    run = await crud_run.update_run(db, run, payload)
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: uuid.UUID,
    reason: str = Query(..., min_length=5, description="Mandatory audit explanation"),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    run = await crud_run.get_run_by_id(db, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    current_change_reason.set(reason)
    await crud_run.delete_run(db, run)
    return Response(status_code=204)
