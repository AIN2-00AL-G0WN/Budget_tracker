"""
app/crud/crud_holiday.py
------------------------
Repository logic for managing company holidays.
"""

from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import CompanyHoliday
from app.schemas.schemas import CompanyHolidayCreate, CompanyHolidayUpdate


async def get_all_holidays(db: AsyncSession) -> Sequence[CompanyHoliday]:
    """Retrieve all non-deleted company holidays."""
    stmt = select(CompanyHoliday).where(CompanyHoliday.is_deleted == False).order_by(CompanyHoliday.holiday_date)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_holidays_in_range(db: AsyncSession, start_date: date, end_date: date) -> list[date]:
    """Return a list of dates representing actual company holidays within a specific date range."""
    stmt = select(CompanyHoliday.holiday_date).where(
        CompanyHoliday.is_deleted == False,
        CompanyHoliday.holiday_date >= start_date,
        CompanyHoliday.holiday_date <= end_date,
    ).order_by(CompanyHoliday.holiday_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_holiday(db: AsyncSession, payload: CompanyHolidayCreate) -> CompanyHoliday:
    """Create a new company holiday."""
    holiday = CompanyHoliday(**payload.model_dump())
    db.add(holiday)
    await db.flush()
    await db.refresh(holiday)
    return holiday


async def update_holiday(db: AsyncSession, holiday: CompanyHoliday, payload: CompanyHolidayUpdate) -> CompanyHoliday:
    """Update an existing company holiday."""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(holiday, field, value)
    db.add(holiday)
    await db.flush()
    await db.refresh(holiday)
    return holiday


async def delete_holiday(db: AsyncSession, holiday: CompanyHoliday) -> None:
    """Soft delete a company holiday."""
    holiday.is_deleted = True
    db.add(holiday)
    await db.flush()
