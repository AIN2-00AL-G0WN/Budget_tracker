"""
app/services/admin_logger.py
-----------------------------
Lightweight service for recording deliberate admin management actions.

Unlike the ORM-level `audit_logger`, this module is called **explicitly**
from router code after a successful admin operation.  It captures *intent*
(what the admin decided to do) rather than raw DB diffs.

Usage
-----
    from app.services.admin_logger import log_admin_action
    from app.models.models import AdminActionType

    await log_admin_action(
        db,
        actor=admin,
        action=AdminActionType.USER_PROVISION,
        target_id=new_user.id,
        target_name=new_user.username,
        detail={"is_admin": new_user.is_admin},
    )
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AdminActionLog, AdminActionType
from app.models.models import User

logger = logging.getLogger(__name__)


async def log_admin_action(
    db: AsyncSession,
    *,
    actor: User,
    action: AdminActionType,
    target_id: uuid.UUID | None = None,
    target_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AdminActionLog:
    """
    Append a row to `admin_action_logs`.

    Parameters
    ----------
    db          : Active async session (same transaction as the action itself).
    actor       : The admin User performing the action.
    action      : One of the AdminActionType enum values.
    target_id   : UUID of the affected user / entity (nullable for global actions).
    target_name : Denormalized display name of the target (preserved even if the
                  target account is later deleted).
    detail      : Arbitrary structured context dict, e.g. {"old_role": False, "new_role": True}.

    Returns
    -------
    The persisted AdminActionLog instance (id populated after flush).
    """
    entry = AdminActionLog(
        actor_id=actor.id,
        actor_name=actor.username,
        action=action,
        target_id=target_id,
        target_name=target_name,
        detail=detail or {},
    )
    db.add(entry)
    await db.flush()

    logger.info(
        "ADMIN_ACTION actor=%s action=%s target=%s",
        actor.username,
        action.value,
        target_name or str(target_id),
    )
    return entry
