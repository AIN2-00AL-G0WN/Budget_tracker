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

from app.models.models import Notification, Team, TeamStatus, User

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

    # Find teams expiring soon that are still active
    sd_result = await db.execute(
        select(Team).where(
            Team.end_date <= threshold,
            Team.end_date >= today,
            Team.status == TeamStatus.IN_PROGRESS,
            Team.is_deleted == False,  # noqa: E712
        )
    )
    expiring = sd_result.scalars().all()

    if not expiring:
        logger.debug("Deadline check: no expiring teams found")
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
            f"⚠ Deadline Alert: Team '{sd.name}' is ending {label} "
            f"({sd.end_date}). Please review budget status."
        )
        for admin in admins:
            already_sent = await db.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.user_id == admin.id,
                    Notification.created_at >= today_start,
                    Notification.created_at < today_end,
                    Notification.message.contains(f"Team '{sd.name}'"),
                )
            )
            already_sent_count = already_sent.scalar()  # Bug #1 fix: cache scalar, only call once
            if already_sent_count and already_sent_count > 0:
                skipped += 1
                continue

            notifications.append(
                Notification(user_id=admin.id, message=msg, is_read=False)
            )
            created += 1

    if notifications:
        db.add_all(notifications)
        # The scheduler opens its own session (not via get_db) which does NOT
        # auto-commit on context-manager exit — we must commit explicitly here.
        await db.commit()

    logger.info(
        "Deadline job: created %d notification(s), skipped %d duplicate(s) "
        "for %d expiring team(s)",
        created,
        skipped,
        len(expiring),
    )
