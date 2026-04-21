"""
app/api/v1/endpoints/business_units.py
--------------------------------------
HTTP Controller for Business Unit resources (Layer 1).
"""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_admin_user, get_current_user
from app.core.database import get_db
from app.crud import crud_business_unit
from app.models.models import User
from app.schemas.schemas import (
    BusinessUnitCreate,
    BusinessUnitOut,
    BusinessUnitUpdate,
)

router = APIRouter(prefix="/business-units", tags=["business_units"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[BusinessUnitOut])
async def list_business_units(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BusinessUnitOut]:
    """Retrieve all business units."""
    return await crud_business_unit.get_all_business_units(db)


@router.post("", response_model=BusinessUnitOut, status_code=status.HTTP_201_CREATED)
async def create_business_unit(
    payload: BusinessUnitCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessUnitOut:
    """Create a new business unit (Admin only)."""
    existing = await crud_business_unit.get_business_unit_by_name(db, payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business Unit with this name already exists"
        )
    return await crud_business_unit.create_business_unit(db, payload)


@router.get("/{bu_id}", response_model=BusinessUnitOut)
async def get_business_unit(
    bu_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessUnitOut:
    """Retrieve a specific business unit by ID."""
    bu = await crud_business_unit.get_business_unit_by_id(db, bu_id)
    if not bu:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business Unit not found")
    return bu


@router.patch("/{bu_id}", response_model=BusinessUnitOut)
async def update_business_unit(
    bu_id: uuid.UUID,
    payload: BusinessUnitUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessUnitOut:
    """Update a business unit (Admin only)."""
    bu = await crud_business_unit.get_business_unit_by_id(db, bu_id)
    if not bu:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business Unit not found")

    if payload.name and payload.name != bu.name:
        existing = await crud_business_unit.get_business_unit_by_name(db, payload.name)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business Unit with this name already exists"
            )

    return await crud_business_unit.update_business_unit(db, bu, payload)


@router.delete("/{bu_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_unit(
    bu_id: uuid.UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a business unit (Admin only)."""
    bu = await crud_business_unit.get_business_unit_by_id(db, bu_id)
    if not bu:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business Unit not found")

    await crud_business_unit.delete_business_unit(db, bu)
    return Response(status_code=204)
