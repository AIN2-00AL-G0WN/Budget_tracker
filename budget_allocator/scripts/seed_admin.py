"""
scripts/seed_admin.py
---------------------
One-shot script to create a dummy admin user for MFA/authenticator testing.

Usage (from the budget_allocator/ directory):
    python scripts/seed_admin.py

What it does
------------
1. Connects to the Postgres DB using the same settings as the app.
2. Checks if the admin already exists — skips if so.
3. Creates the user with:
     username  : test_admin
     password  : Rdl@12345  (pre-hashed with Argon2id — same as the app)
     is_admin  : True
     requires_password_change : False  ← so we can log in directly
     totp_secret : None                ← MFA NOT yet set up; setup flow will add it
4. Prints a one-time setup token you can POST to /api/v1/auth/setup to complete
   MFA enrollment and get the TOTP QR code.
"""

from __future__ import annotations

import asyncio
import sys
import os
import io

# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeErrors
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Make sure the project root is on sys.path so `app.*` imports resolve ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password, create_token
from app.models.models import User

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADMIN_USERNAME = "tejasbhat2001@gmail.com"
ADMIN_PASSWORD = "Rdl@12345"

# ---------------------------------------------------------------------------
# Async seed function
# ---------------------------------------------------------------------------

async def seed() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            # Check if admin already exists
            result = await session.execute(
                select(User).where(User.username == ADMIN_USERNAME)
            )
            existing: User | None = result.scalar_one_or_none()

            if existing:
                print(f"\n[WARN]  User '{ADMIN_USERNAME}' already exists (id={existing.id}).")
                print("   Resetting admin account to factory defaults and generating a fresh setup token...\n")
                existing.hashed_password = hash_password(ADMIN_PASSWORD)
                existing.requires_password_change = True
                existing.totp_secret = None
                existing.token_version += 1
                user = existing
            else:
                # Create the admin user
                user = User(
                    username=ADMIN_USERNAME,
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    is_admin=True,
                    is_active=True,
                    requires_password_change=True,  # Forces setup flow
                    totp_secret=None,               # MFA enrolled via /auth/setup
                    token_version=0,
                )
                session.add(user)
                await session.flush()  # Get the auto-generated UUID
                print(f"\n[OK]  Admin user created!")
                print(f"    Username : {ADMIN_USERNAME}")
                print(f"    Password : {ADMIN_PASSWORD}")
                print(f"    User ID  : {user.id}")
                print(f"    is_admin : True")
                print(f"    MFA      : NOT YET SET UP — use /auth/setup to enroll\n")

    # Generate a setup token AFTER the user is committed
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username == ADMIN_USERNAME)
        )
        user = result.scalar_one()

        setup_token = create_token(
            user_id=user.id,
            kind="setup",
            token_version=user.token_version,
        )

    await engine.dispose()

    print("=" * 70)
    print("  NEXT STEP — Complete MFA Setup")
    print("=" * 70)
    print()
    print("  POST http://localhost:8000/api/v1/auth/setup")
    print("  Headers: Authorization: Bearer <setup_token_below>")
    print("  Body:    { \"new_password\": \"Rdl@12345\" }")
    print()
    print("  Setup Token (valid 72 h):")
    print(f"\n  {setup_token}\n")
    print("=" * 70)
    print()
    print("  After calling /auth/setup you will receive a  totp_provisioning_uri.")
    print("  Open it or extract the 'secret=' param and add it to your")
    print("  Authenticator app (Google Authenticator / Authy / etc.) manually.")
    print()
    print("  [?]  What account/email do you use in the Authenticator app?")
    print("      The TOTP entry will be labelled:")
    print(f"      BudgetAllocator:{ADMIN_USERNAME}")
    print()
    print("  You do NOT need a real Gmail — the label is just cosmetic.")
    print("  When prompted by the app for an 'account', type anything you like,")
    print("  e.g.  test_admin@budgetallocator.local")
    print()
    print("  Once added, log in with:")
    print("  POST /api/v1/auth/login")
    print("  { \"username\": \"test_admin\", \"password\": \"Rdl@12345\", \"totp_code\": \"<6-digit code>\" }")
    print()


if __name__ == "__main__":
    asyncio.run(seed())
