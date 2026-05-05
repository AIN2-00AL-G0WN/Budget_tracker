"""
app/services/calendar_service.py
--------------------------------
Service for business day math natively utilizing the Company Holiday repository dynamically.

Functions
---------
calculate_working_days  — low-level weekday counter (used internally and by tests)
resolve_budget_duration — full duration resolution + validation for budget endpoints;
                          raises ValueError so the controller can map it to HTTP 422.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import crud_holiday


async def calculate_working_days(
    start_date: date,
    end_date: date,
    db: AsyncSession,
    business_unit: str,
) -> int:
    """
    Calculate the number of standard working days (Mon-Fri) between start_date
    and end_date (inclusive), minus any company holidays that fall on a weekday.
    """
    if start_date > end_date:
        return 0

    # 1. Fetch holidays within range
    holidays = await crud_holiday.get_holidays_in_range(db, start_date, end_date, business_unit)
    holiday_set = set(holidays)

    # 2. Iterate and count non-holiday weekdays
    working_days = 0
    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() < 5 and current_date not in holiday_set:
            working_days += 1
        current_date += timedelta(days=1)

    return working_days


class DurationResult(NamedTuple):
    """Resolved and validated duration values for a budget calculation."""
    duration_in_days: float       # The final value to use in the calculation
    expected_working_days: float  # Holiday-adjusted working days (informational)
    total_calendar_days: float    # Full span from start → end inclusive


async def resolve_budget_duration(
    *,
    start_date: date,
    end_date: date,
    requested_duration: float | None,
    run_id,
    db: AsyncSession,
) -> DurationResult:
    """
    Service-layer function that owns all duration business logic for budgets.

    Steps
    -----
    1. Look up the Business Unit name for the given run (via the Run→Team→Family→BU join).
    2. Calculate the holiday-adjusted working days (``expected_working_days``).
    3. Calculate the maximum possible days from the date range (``total_calendar_days``).
    4. Validate ``requested_duration``:
         - If None  → default to ``expected_working_days``
         - If < 0   → raise ValueError
         - If > total_calendar_days → raise ValueError
         - Otherwise → accept as-is (supports compressed sprints, weekend work, etc.)
    5. Return a ``DurationResult`` NamedTuple.

    Raises
    ------
    ValueError — on invalid duration; the controller maps this to HTTP 422.
    RuntimeError — if the run has no associated Business Unit (data integrity issue).
    """
    from app.models.models import Run, Team, Family, BusinessUnit

    # Step 1: Resolve Business Unit name from the run hierarchy
    stmt = (
        select(BusinessUnit.name)
        .select_from(Run)
        .join(Team, Run.team_id == Team.id)
        .join(Family, Team.family_id == Family.id)
        .join(BusinessUnit, Family.business_unit_id == BusinessUnit.id)
        .where(Run.id == run_id)
    )
    bu_result = await db.execute(stmt)
    business_unit = bu_result.scalar_one_or_none()
    if business_unit is None:
        raise RuntimeError(
            f"Run {run_id} has no associated Business Unit. Check data integrity."
        )

    # Step 2: Holiday-adjusted working days (informational lower-bound reference)
    expected_working_days = await crud_holiday.calculate_working_days(
        db, start_date, end_date, business_unit
    )

    # Step 3: Total calendar days (absolute upper bound)
    total_calendar_days = float((end_date - start_date).days + 1)

    # Step 4: Validate and resolve the requested duration
    # Valid range: 0  <=  duration  <=  total_calendar_days
    if requested_duration is None:
        # Default to the standard holiday-adjusted working days
        duration = expected_working_days
    elif requested_duration < 0:
        raise ValueError("Duration cannot be negative.")
    elif requested_duration > total_calendar_days:
        raise ValueError(
            f"Duration ({requested_duration}) cannot exceed the total calendar days "
            f"({int(total_calendar_days)}) between the start and end dates."
        )
    else:
        duration = requested_duration

    return DurationResult(
        duration_in_days=duration,
        expected_working_days=expected_working_days,
        total_calendar_days=total_calendar_days,
    )
