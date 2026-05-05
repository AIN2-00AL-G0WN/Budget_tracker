"""
app/crud/crud_auth_log.py
--------------------------
Repository (Data-Access Layer) for AuthLog and AdminActionLog entities.

Design rules
~~~~~~~~~~~~
* Every function is async and accepts an ``AsyncSession`` as its first argument.
* Functions return ORM objects or sequences — they NEVER raise HTTP exceptions.
* Controllers import these functions instead of writing inline SQLAlchemy queries.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AdminActionLog, AuthEventType, AuthLog


# ===========================================================================
# Auth Logs
# ===========================================================================


async def log_auth_event(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    username: str | None,
    event_type: AuthEventType,
    ip_address: str | None,
) -> None:
    """
    Append an authentication event record to ``auth_logs``.

    Uses ``db.add()`` without flushing — the surrounding unit-of-work
    (``async with session.begin()`` or an explicit ``db.commit()``) will
    persist it.  The caller is responsible for committing.
    """
    db.add(
        AuthLog(
            user_id=user_id,
            username=username,
            event_type=event_type,
            ip_address=ip_address,
        )
    )


async def get_auth_logs(
    db: AsyncSession,
    *,
    username: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[AuthLog]:
    """
    Return authentication logs ordered by most-recent first.

    Parameters
    ----------
    username   : Optional case-insensitive partial match on ``auth_logs.username``.
    event_type : Optional exact match on ``auth_logs.event_type``.
    """
    stmt = select(AuthLog).order_by(desc(AuthLog.timestamp))
    if username:
        stmt = stmt.where(AuthLog.username.ilike(f"%{username}%"))
    if event_type:
        stmt = stmt.where(AuthLog.event_type == event_type)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


# ===========================================================================
# Admin Action Logs
# ===========================================================================


async def get_admin_action_logs(
    db: AsyncSession,
    *,
    actor_name: str | None = None,
    action: str | None = None,
    target_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[AdminActionLog]:
    """
    Return admin intent logs ordered by most-recent first.

    Parameters
    ----------
    actor_name  : Optional case-insensitive partial match on the admin's username.
    action      : Optional exact match on the action type enum value.
    target_name : Optional case-insensitive partial match on the target username.
    """
    stmt = select(AdminActionLog).order_by(desc(AdminActionLog.timestamp))
    if actor_name:
        stmt = stmt.where(AdminActionLog.actor_name.ilike(f"%{actor_name}%"))
    if action:
        stmt = stmt.where(AdminActionLog.action == action)
    if target_name:
        stmt = stmt.where(AdminActionLog.target_name.ilike(f"%{target_name}%"))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()
