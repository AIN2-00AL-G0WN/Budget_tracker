"""
app/crud/crud_lookups.py
--------------------------
Highly optimized lookup queries for frontend dropdowns.
These queries explicitly fetch only required columns to eliminate overhead.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Family, Team, User, BusinessUnit

async def get_family_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str, str]]:
    """Fetch lightweight ID, Name, and Business Unit for all active Families."""
    stmt = (
        select(Family.id, Family.name, BusinessUnit.name.label("business_unit_name"))
        .join(BusinessUnit, Family.business_unit_id == BusinessUnit.id)
        .where(Family.is_deleted == False)
        .order_by(Family.name)
    )
    result = await db.execute(stmt)
    return result.all()

async def get_team_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str]]:
    """Fetch lightweight ID and Name for all active Teams."""
    stmt = select(Team.id, Team.name).where(Team.is_deleted == False).order_by(Team.name)
    result = await db.execute(stmt)
    return result.all()

async def get_user_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str]]:
    """Fetch lightweight ID and Username for all active Users."""
    stmt = select(User.id, User.username).where(User.is_active == True).order_by(User.username)
    result = await db.execute(stmt)
    return result.all()

async def get_distinct_business_units(db: AsyncSession) -> Sequence[str]:
    """Fetch a unique list of all business units currently in use by active families."""
    stmt = (
        select(BusinessUnit.name)
        .join(Family, BusinessUnit.id == Family.business_unit_id)
        .where(Family.is_deleted == False)
        .distinct()
        .order_by(BusinessUnit.name)
    )
    result = await db.execute(stmt)
    return result.scalars().all()
