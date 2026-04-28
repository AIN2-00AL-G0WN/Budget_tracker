"""
app/api/v1/endpoints/families.py
---------------------------------
HTTP Controller for Family resources (formerly projects.py).
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
    FamilyCreate,
    FamilyOut,
    FamilyUpdate,
    BudgetSummaryOut,
)

router = APIRouter(prefix="/families", tags=["families"])
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedResponse[FamilyOut])
async def list_families(
    business_unit_id: uuid.UUID | None = Query(None, description="Filter by business unit ID"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[FamilyOut]:
    total = await crud_family.count_active_families(db, business_unit_id=business_unit_id)
    items = await crud_family.get_all_families_paginated(db, business_unit_id=business_unit_id, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=FamilyOut, status_code=status.HTTP_201_CREATED)
async def create_family(
    payload: FamilyCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyOut:
    family = await crud_family.create_family(db, payload)
    logger.info("Family created: %s (%s)", family.name, family.id)
    return family  # type: ignore[return-value]


@router.get("/{family_id}", response_model=FamilyOut)
async def get_family(
    family_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyOut:
    family = await crud_family.get_family_by_id(db, family_id, load_teams=True)
    if not family:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family not found")
    return family  # type: ignore[return-value]


@router.patch("/{family_id}", response_model=FamilyOut)
async def update_family(
    family_id: uuid.UUID,
    payload: FamilyUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> FamilyOut:
    family = await crud_family.get_family_by_id(db, family_id)
    if not family:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family not found")
    family = await crud_family.update_family(db, family, payload)
    return family  # type: ignore[return-value]


@router.delete("/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_family(
    family_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    family = await crud_family.get_family_by_id(db, family_id)
    if not family:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family not found")
    await crud_family.delete_family(db, family)
    return Response(status_code=204)


@router.get("/{family_id}/summary", tags=["families"], response_model=BudgetSummaryOut)
async def get_family_summary(
    family_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """Aggregate budget figures across all non-deleted teams for a family."""
    family = await crud_family.get_family_by_id(db, family_id)
    if not family:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Family not found")

    summary_data = await crud_family.get_family_budget_summary(db, family_id)
    if not summary_data:
        return BudgetSummaryOut(
            tc_count=0, duration_wks=0.0, manual_hc=0, automation_hc=0,
            manual_hc_cost=0.0, automation_hc_cost=0.0, lead_cost=0.0,
            sqpm_cost_boise=0.0, pl_cost=0.0, per_wqe_cost=0.0,
            asqpm_cost=0.0, lab_tech_manager_cost=0.0, project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)
