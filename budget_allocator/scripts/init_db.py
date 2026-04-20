"""
scripts/init_db.py
------------------
Enterprise Initialization Script.
Handles safe sequential execution of Database Migrations and foundational Seeding.
"""
import asyncio
import io
import logging
import os
import sys

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make sure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.config import Config
from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.setup_v1 import seed_initial_data
from app.core.security import hash_password, create_token
from app.models.models import User

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")
logger = logging.getLogger("InitDB")

def run_migrations():
    """Run Alembic migrations synchronously."""
    logger.info("Executing Alembic migrations...")
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    logger.info("Migrations complete.")

async def seed_admin(session: AsyncSession):
    """Seed the default Admin user safely."""
    result = await session.execute(
        select(User).where(User.username == settings.admin_username)
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.info(f"Admin User '{settings.admin_username}' already exists. Skipping.")
        return

    # Create the admin user
    user = User(
        username=settings.admin_username,
        hashed_password=hash_password(settings.admin_password),
        is_admin=True,
        is_active=True,
        requires_password_change=True,
        totp_secret=None,
        token_version=0,
    )
    session.add(user)
    await session.flush()
    
    # Generate setup token
    setup_token = create_token(user_id=user.id, kind="setup", token_version=user.token_version)
    
    logger.info("=" * 70)
    logger.info("  [!] INITIAL SETUP REQUIRED")
    logger.info("=" * 70)
    logger.info(f"  System created default admin user: {settings.admin_username}")
    logger.info("  Please complete MFA Setup via API (valid for 72h):")
    logger.info(f"  Token: {setup_token}")
    logger.info("=" * 70)

async def seed_data():
    # 2. Database Session Initialization
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Seed System State (Families, Lookups, RateCards)
        logger.info("Seeding system globals...")
        await seed_initial_data(session)
        
        # Seed Admin User
        logger.info("Verifying admin accounts...")
        await seed_admin(session)
        await session.commit()

    await engine.dispose()
    logger.info("Initialization sequence completed successfully.")

def main():
    # 1. Migrations must run first
    run_migrations()
    
    # 2. Run background seeding
    asyncio.run(seed_data())

if __name__ == "__main__":
    main()
