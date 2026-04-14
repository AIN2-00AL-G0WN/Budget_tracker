"""
app/api/v1/endpoints/test_runs.py
---------------------------------
HTTP Controller for TestRun resources.
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.crud import crud_project, crud_test_run
from app.models.models import User
from app.schemas.schemas import (
    PaginatedResponse,
    TestRunCreate,
    TestRunOut,
    TestRunUpdate,
)
from app.api.dependencies.filters import BudgetFilterParams, get_budget_filters

router = APIRouter(prefix="/test-runs", tags=["test-runs"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedResponse[TestRunOut])
async def list_test_runs(
    team_id: uuid.UUID | None = Query(None, description="Filter by parent team (subdivision) ID"),
    filters: BudgetFilterParams = Depends(get_budget_filters),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TestRunOut]:
    total = await crud_test_run.count_active_test_runs(db, sub_division_id=team_id, filters=filters)
    items = await crud_test_run.get_test_runs_paginated(db, sub_division_id=team_id, filters=filters, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=TestRunOut, status_code=status.HTTP_201_CREATED)
async def create_test_run(
    team_id: uuid.UUID = Query(..., description="Parent team (subdivision) ID"),
    payload: TestRunCreate = ...,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> TestRunOut:
    parent = await crud_project.get_team_by_id(db, team_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team (SubDivision) not found")

    tr = await crud_test_run.create_test_run(db, team_id, payload)
    return tr


@router.get("/{test_run_id}", response_model=TestRunOut)
async def get_test_run(
    test_run_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TestRunOut:
    tr = await crud_test_run.get_test_run_by_id(db, test_run_id)
    if not tr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TestRun not found")
    return tr


@router.patch("/{test_run_id}", response_model=TestRunOut)
async def update_test_run(
    test_run_id: uuid.UUID,
    payload: TestRunUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> TestRunOut:
    tr = await crud_test_run.get_test_run_by_id(db, test_run_id)
    if not tr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TestRun not found")
    tr = await crud_test_run.update_test_run(db, tr, payload)
    return tr


@router.delete("/{test_run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_run(
    test_run_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    tr = await crud_test_run.get_test_run_by_id(db, test_run_id)
    if not tr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TestRun not found")
    await crud_test_run.delete_test_run(db, tr)
    return Response(status_code=204)
