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


async def get_holidays_in_range(db: AsyncSession, start_date: date, end_date: date, business_unit: str) -> list[date]:
    """Return a list of dates representing actual company holidays within a specific date range."""
    from sqlalchemy import or_
    from app.models.models import BusinessUnit

    # Resolve the BU name to its UUID
    bu_result = await db.execute(
        select(BusinessUnit.id).where(BusinessUnit.name == business_unit)
    )
    bu_id = bu_result.scalar_one_or_none()

    stmt = select(CompanyHoliday.holiday_date).where(
        CompanyHoliday.is_deleted == False,
        CompanyHoliday.holiday_date >= start_date,
        CompanyHoliday.holiday_date <= end_date,
        or_(CompanyHoliday.business_unit_id.is_(None), CompanyHoliday.business_unit_id == bu_id)
    ).order_by(CompanyHoliday.holiday_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def calculate_working_days(db: AsyncSession, start_date: date, end_date: date, business_unit: str) -> float:
    """
    Calculate the actual working days between start_date and end_date (inclusive),
    excluding Saturdays, Sundays, and any dates mapped in the CompanyHoliday table.
    """
    from datetime import timedelta

    days_range = (end_date - start_date).days + 1
    if days_range <= 0:
        return 0.0

    # Count weekdays (0=Monday ... 4=Friday)
    workdays = 0
    for i in range(days_range):
        current_date = start_date + timedelta(days=i)
        if current_date.weekday() < 5:
            workdays += 1

    # Fetch company holidays for this BU
    holidays = await get_holidays_in_range(db, start_date, end_date, business_unit)
    
    # Subtract holidays that are NOT already weekends
    valid_holiday_count = sum(1 for h in holidays if h.weekday() < 5)

    return max(0.0, float(workdays - valid_holiday_count))


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
