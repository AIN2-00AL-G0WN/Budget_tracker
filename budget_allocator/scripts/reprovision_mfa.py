"""
scripts/reprovision_mfa.py
--------------------------
Re-provisions TOTP / MFA for users whose `totp_secret` is NULL
WITHOUT changing their password.

Run this whenever a user gets the error:
    "MFA is not enabled for this account. Contact an administrator."

Usage (from the budget_allocator/ directory):
    python -m scripts.reprovision_mfa
  -- OR --
    python scripts/reprovision_mfa.py

Output
------
For every affected user the script prints a `totp://` provisioning URI.
Scan it with Google Authenticator / Authy / any TOTP app.
After scanning, the user can immediately call /api/v1/auth/forgot-password.
"""

from __future__ import annotations

import asyncio
import sys
import os
import io

# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeErrors
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import generate_totp_secret, get_totp_uri
from app.models.models import User


async def reprovision() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        # Find all active users with no TOTP secret
        result = await session.execute(
            select(User).where(User.is_active == True)
        )
        users: list[User] = result.scalars().all()

        affected = [u for u in users if not u.totp_secret]

        if not affected:
            print("\n[OK]  All active users already have MFA configured. Nothing to do.\n")
            await engine.dispose()
            return

        print(f"\n[INFO]  Found {len(affected)} user(s) without MFA:\n")

        for user in affected:
            # Generate a brand-new TOTP secret and save it
            secret = generate_totp_secret()
            user.totp_secret = secret
            # Bump token_version so old sessions are invalidated (security best-practice)
            user.token_version += 1
            session.add(user)

            totp_uri = get_totp_uri(secret, user.username)

            print("=" * 70)
            print(f"  Username : {user.username}")
            print(f"  User ID  : {user.id}")
            print()
            print("  ACTION REQUIRED — Scan this URI with your Authenticator app:")
            print()
            print(f"  {totp_uri}")
            print()
            print("  Tip: In Google Authenticator → tap '+' → 'Enter a setup key'")
            print(f"       Account: {user.username}")
            print(f"       Key:     (extract the 'secret=' value from the URI above)")
            print("       Type:    Time-based")
            print()

        await session.commit()
        print("=" * 70)
        print("\n[OK]  MFA provisioned and saved for all affected users.")
        print("      Scan the URI(s) above, then use /api/v1/auth/forgot-password normally.\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(reprovision())
