"""
app/crud/crud_audit.py
--------------------------
Data-Access Layer for Audit Logs.
"""

from __future__ import annotations

from typing import Sequence
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog


async def get_audit_logs(
    db: AsyncSession,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[AuditLog]:
    """Return audit logs, optionally filtered by entity type / ID."""
    q = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()
