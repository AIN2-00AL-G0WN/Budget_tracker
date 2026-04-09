"""
app/scheduler/jobs.py
----------------------
APScheduler job definitions.

Jobs are pure async coroutines that accept a SQLAlchemy async session.
They are registered and triggered by the scheduler setup in `scheduler/setup.py`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Notification, SubDivision, SubDivisionStatus, User

logger = logging.getLogger(__name__)

# How many days before `end_date` we generate an alert
ALERT_DAYS_BEFORE = 3


async def check_deadline_proximity(db: AsyncSession) -> None:
    """
    Daily job (runs at midnight UTC) that inserts Notification rows for any
    SubDivision whose ``end_date`` is within ``ALERT_DAYS_BEFORE`` days.

    Deduplication (Fix #8)
    -----------------------
    Before inserting a notification we verify that no notification containing
    the subdivision name was already sent to this user today (UTC).  This
    prevents duplicate alerts on repeated runs or restarts.

    Targeting strategy (no assignment table exists yet)
    ---------------------------------------------------
    Until a user ↔ subdivision assignment table is built, we alert ALL
    admin users.  Replace this with a proper assignment query when ready.
    """
    # Fix #18: Use UTC date, not local system date
    today: date = datetime.now(timezone.utc).date()
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

    # Midnight boundaries of today in UTC (for deduplication query)
    today_start = datetime(
        today.year, today.month, today.day, tzinfo=timezone.utc
    )
    today_end = today_start + timedelta(days=1)

    notifications: list[Notification] = []
    created = 0
    skipped = 0

    for sd in expiring:
        days_left = (sd.end_date - today).days
        label = "today" if days_left == 0 else f"in {days_left} day(s)"
        msg = (
            f"⚠ Deadline Alert: SubDivision '{sd.name}' is ending {label} "
            f"({sd.end_date}). Please review budget status."
        )
        for admin in admins:
            # Fix #8: Skip if we already notified this admin about this
            # subdivision today (prevents duplicates on re-runs / restarts).
            already_sent = await db.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.user_id == admin.id,
                    Notification.created_at >= today_start,
                    Notification.created_at < today_end,
                    Notification.message.contains(f"SubDivision '{sd.name}'"),
                )
            )
            if already_sent.scalar() and already_sent.scalar() > 0:
                skipped += 1
                continue

            notifications.append(
                Notification(user_id=admin.id, message=msg, is_read=False)
            )
            created += 1

    if notifications:
        db.add_all(notifications)
        await db.commit()

    logger.info(
        "Deadline job: created %d notification(s), skipped %d duplicate(s) "
        "for %d expiring sub-division(s)",
        created,
        skipped,
        len(expiring),
    )
