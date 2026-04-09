"""
app/crud/crud_notification.py
------------------------------
Data-Access Layer (Repository) for the Notification entity.

Notifications are written exclusively by:
* The nightly APScheduler job (deadline-proximity alerts).
* Any future system event.

This module only exposes *read* and *mark-read* operations because creation
is the scheduler's responsibility.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Notification


async def get_notifications_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = 100,
) -> Sequence[Notification]:
    """
    Return all notifications for a user, newest first, capped at ``limit``.
    """
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(desc(Notification.created_at))
        .limit(limit)
    )
    return result.scalars().all()


async def mark_notifications_read(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_ids: list[int],
) -> None:
    """
    Mark a set of notification IDs as read for the given user.

    The ``user_id`` filter prevents users from marking other users'
    notifications as read.
    """
    result = await db.execute(
        select(Notification).where(
            Notification.id.in_(notification_ids),
            Notification.user_id == user_id,
        )
    )
    for notification in result.scalars().all():
        notification.is_read = True
        db.add(notification)
