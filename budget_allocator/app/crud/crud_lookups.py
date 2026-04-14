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

from app.models.models import Project, SubDivision, User

async def get_project_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str]]:
    """Fetch lightweight ID and Name for all active Projects."""
    stmt = select(Project.id, Project.name).where(Project.is_deleted == False).order_by(Project.name)
    result = await db.execute(stmt)
    return result.all()

async def get_team_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str]]:
    """Fetch lightweight ID and Name for all active SubDivisions (Teams)."""
    stmt = select(SubDivision.id, SubDivision.name).where(SubDivision.is_deleted == False).order_by(SubDivision.name)
    result = await db.execute(stmt)
    return result.all()

async def get_user_lookups(db: AsyncSession) -> Sequence[tuple[uuid.UUID, str]]:
    """Fetch lightweight ID and Username for all active Users."""
    stmt = select(User.id, User.username).where(User.is_active == True).order_by(User.username)
    result = await db.execute(stmt)
    return result.all()

async def get_distinct_business_units(db: AsyncSession) -> Sequence[str]:
    """Fetch a unique list of all active business units."""
    stmt = select(Project.business_unit).where(Project.is_deleted == False).distinct().order_by(Project.business_unit)
    result = await db.execute(stmt)
    return result.scalars().all()
