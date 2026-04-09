"""
app/scheduler/jobs.py
----------------------
APScheduler job definitions.

Jobs are pure async coroutines that accept a SQLAlchemy async session.
They are registered and triggered by the scheduler setup in `scheduler/setup.py`.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Notification, SubDivision, SubDivisionStatus, User

logger = logging.getLogger(__name__)

# How many days before `end_date` we generate an alert
ALERT_DAYS_BEFORE = 3


async def check_deadline_proximity(db: AsyncSession) -> None:
    """
    Daily job (runs at midnight) that inserts Notification rows for any
    SubDivision whose `end_date` is within `ALERT_DAYS_BEFORE` days.

    Targeting strategy (no assignment table exists yet)
    ---------------------------------------------------
    Until a user ↔ subdivision assignment table is built, we alert ALL
    admin users.  Replace this with a proper assignment query when ready.
    """
    today = date.today()
    threshold = today + timedelta(days=ALERT_DAYS_BEFORE)

    # Find sub-divisions expiring soon that are still active
    sd_result = await db.execute(
        select(SubDivision).where(
            SubDivision.end_date <= threshold,
            SubDivision.end_date >= today,
            SubDivision.status == SubDivisionStatus.IN_PROGRESS,
        )
    )
    expiring = sd_result.scalars().all()

    if not expiring:
        logger.debug("Deadline check: no expiring sub-divisions found")
        return

    # Fetch all active admin users to notify
    user_result = await db.execute(
        select(User).where(User.is_active == True, User.is_admin == True)  # noqa: E712
    )
    admins = user_result.scalars().all()

    notifications: list[Notification] = []
    for sd in expiring:
        days_left = (sd.end_date - today).days
        label = "today" if days_left == 0 else f"in {days_left} day(s)"
        for admin in admins:
            msg = (
                f"⚠ Deadline Alert: SubDivision '{sd.name}' is ending {label} "
                f"({sd.end_date}). Please review budget status."
            )
            notifications.append(
                Notification(user_id=admin.id, message=msg, is_read=False)
            )

    db.add_all(notifications)
    await db.commit()

    logger.info(
        "Deadline job: created %d notification(s) for %d expiring sub-division(s)",
        len(notifications),
        len(expiring),
    )
