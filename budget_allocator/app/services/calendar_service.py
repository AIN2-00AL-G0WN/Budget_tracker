"""
app/services/calendar_service.py
--------------------------------
Service for business day math natively utilizing the Company Holiday repository dynamically.
"""

from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud import crud_holiday


async def calculate_working_days(start_date: date, end_date: date, db: AsyncSession) -> int:
    """
    Calculate the number of standard working days (Mon-Fri) between start_date
    and end_date (inclusive), minus any company holidays that fall on a weekday.
    """
    if start_date > end_date:
        return 0

    # 1. Fetch holidays within range natively tracking bounds
    holidays = await crud_holiday.get_holidays_in_range(db, start_date, end_date)
    holiday_set = set(holidays)

    # 2. Iterate and evaluate working boundaries explicitly
    working_days = 0
    current_date = start_date

    while current_date <= end_date:
        # weekday() returns 0 (Mon) to 6 (Sun)
        is_weekday = current_date.weekday() < 5
        is_holiday = current_date in holiday_set

        if is_weekday and not is_holiday:
            working_days += 1

        current_date += timedelta(days=1)

    return working_days
