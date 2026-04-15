"""
app/api/v1/endpoints/lookups.py
-------------------------------
Optimized endpoints for frontend dropdown population.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.database import get_db
from app.crud import crud_lookups
from app.models.models import User
from app.schemas.lookups import LookupItemOut, FamilyLookupOut

router = APIRouter(prefix="/lookups", tags=["lookups"])

@router.get("/families", response_model=List[FamilyLookupOut[uuid.UUID]])
async def get_family_lookups(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[dict]:
    rows = await crud_lookups.get_family_lookups(db)
    return [{"id": row.id, "name": row.name, "business_unit": row.business_unit} for row in rows]

@router.get("/teams", response_model=List[LookupItemOut[uuid.UUID]])
async def get_team_lookups(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[dict]:
    rows = await crud_lookups.get_team_lookups(db)
    return [{"id": row.id, "name": row.name} for row in rows]

@router.get("/users", response_model=List[LookupItemOut[uuid.UUID]])
async def get_user_lookups(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[dict]:
    rows = await crud_lookups.get_user_lookups(db)
    return [{"id": row.id, "name": row.username} for row in rows]

@router.get("/business-units", response_model=List[str])
async def get_business_unit_lookups(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    return await crud_lookups.get_distinct_business_units(db)
