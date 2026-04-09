"""
app/crud/crud_totp.py
---------------------
Data-Access Layer for TOTP replay-attack prevention (Fix #3).

After a TOTP code passes ``pyotp.TOTP.verify()``, it must be "consumed" here
so the same 6-digit code cannot be replayed within the ±90-second validity
window.

Each ``ConsumedTOTPCode`` row carries an ``expires_at`` timestamp.  We prune
expired rows on every login (opportunistic cleanup) to keep the table tiny.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ConsumedTOTPCode

# A TOTP interval is 30s; valid_window=1 means ±1 interval → 90s total validity.
_CODE_TTL_SECONDS = 90


async def is_code_consumed(db: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """
    Return ``True`` if this ``code`` was already verified for ``user_id``
    within the current validity window.

    Must be called **before** ``consume_code`` and only after pyotp verifies
    the code is mathematically correct.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ConsumedTOTPCode).where(
            ConsumedTOTPCode.user_id == user_id,
            ConsumedTOTPCode.code == code,
            ConsumedTOTPCode.expires_at > now,
        )
    )
    return result.scalar_one_or_none() is not None


async def consume_code(db: AsyncSession, user_id: uuid.UUID, code: str) -> None:
    """
    Mark a TOTP code as consumed for the next ``_CODE_TTL_SECONDS`` seconds.

    Call this immediately after a successful TOTP verification, before the
    response is returned.  The row is flushed into the same transaction as
    the login, so it is rolled back atomically if the transaction fails.
    """
    now = datetime.now(timezone.utc)
    db.add(
        ConsumedTOTPCode(
            user_id=user_id,
            code=code,
            expires_at=now + timedelta(seconds=_CODE_TTL_SECONDS),
        )
    )
    # Flush immediately so the consumed record is visible to any concurrent
    # request that might race with the same code.
    await db.flush()


async def purge_expired_codes(db: AsyncSession) -> None:
    """
    Delete all expired ``ConsumedTOTPCode`` rows.

    Called opportunistically at the start of each login to keep the table
    small without a dedicated maintenance job.
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        delete(ConsumedTOTPCode).where(ConsumedTOTPCode.expires_at <= now)
    )
