"""
app/crud/crud_business_unit.py
------------------------------
Data-Access Layer for Business Unit entity.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import BusinessUnit
from app.schemas.schemas import BusinessUnitCreate, BusinessUnitUpdate
from app.core.context import current_username


async def get_all_business_units(db: AsyncSession) -> Sequence[BusinessUnit]:
    """Return all non-deleted business units."""
    stmt = select(BusinessUnit).where(BusinessUnit.is_deleted == False).order_by(BusinessUnit.name)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_business_unit_by_id(db: AsyncSession, bu_id: uuid.UUID) -> BusinessUnit | None:
    """Fetch a single non-deleted business unit by ID."""
    stmt = select(BusinessUnit).where(
        BusinessUnit.id == bu_id,
        BusinessUnit.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_business_unit_by_name(db: AsyncSession, name: str) -> BusinessUnit | None:
    """Fetch a single non-deleted business unit by name."""
    stmt = select(BusinessUnit).where(
        BusinessUnit.name == name,
        BusinessUnit.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_business_unit(db: AsyncSession, payload: BusinessUnitCreate) -> BusinessUnit:
    """Persist a new Business Unit."""
    data = payload.model_dump()
    data["created_by_name"] = current_username.get()
    data["updated_by_name"] = current_username.get()
    bu = BusinessUnit(**data)
    db.add(bu)
    await db.flush()
    await db.refresh(bu)
    return bu


async def update_business_unit(
    db: AsyncSession,
    bu: BusinessUnit,
    payload: BusinessUnitUpdate,
) -> BusinessUnit:
    """Apply a partial update to an already-fetched BusinessUnit ORM object."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(bu, field, value)
    bu.updated_by_name = current_username.get()
    db.add(bu)
    await db.flush()
    await db.refresh(bu)
    return bu


async def delete_business_unit(db: AsyncSession, bu: BusinessUnit) -> None:
    """Soft-delete a Business Unit."""
    bu.is_deleted = True
    bu.updated_by_name = current_username.get()
    db.add(bu)
    await db.flush()
