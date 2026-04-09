"""
app/crud/crud_rate_card.py
--------------------------
Data-Access Layer (Repository) for the RateCard entity.

RateCards use an auto-increment integer PK (not UUID) because they are a
small, admin-managed lookup table — integer IDs are fine here.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RateCard
from app.schemas.schemas import RateCardCreate, RateCardUpdate


async def get_all_rate_cards(db: AsyncSession) -> Sequence[RateCard]:
    """Return all rate cards ordered by key name."""
    result = await db.execute(select(RateCard).order_by(RateCard.key_name))
    return result.scalars().all()


async def get_rate_card_by_id(db: AsyncSession, rc_id: int) -> RateCard | None:
    """Fetch a single RateCard by its integer primary key."""
    result = await db.execute(select(RateCard).where(RateCard.id == rc_id))
    return result.scalar_one_or_none()


async def get_rate_card_by_key(db: AsyncSession, key_name: str) -> RateCard | None:
    """
    Fetch a RateCard by its unique ``key_name``.

    Used before creation to enforce uniqueness at the application layer
    (returning a clear 409 rather than a DB-level unique-constraint error).
    """
    result = await db.execute(select(RateCard).where(RateCard.key_name == key_name))
    return result.scalar_one_or_none()


async def create_rate_card(db: AsyncSession, payload: RateCardCreate) -> RateCard:
    """Persist a new RateCard row and flush to get the auto-incremented ID."""
    rc = RateCard(**payload.model_dump())
    db.add(rc)
    await db.flush()
    await db.refresh(rc)   # Fix #15: reload updated_at from DB
    return rc


async def update_rate_card(
    db: AsyncSession,
    rc: RateCard,
    payload: RateCardUpdate,
) -> RateCard:
    """Apply a partial update to an already-fetched RateCard ORM object."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(rc, field, value)
    db.add(rc)
    await db.flush()          # Trigger UPDATE so DB sets updated_at
    await db.refresh(rc)      # Fix #15: reload updated_at before returning
    return rc


async def delete_rate_card(db: AsyncSession, rc: RateCard) -> None:
    """Delete a RateCard row."""
    await db.delete(rc)
