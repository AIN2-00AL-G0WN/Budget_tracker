import asyncio
import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import SessionLocal
from sqlalchemy import select
from app.models.models import User, AuthLog

async def check_user():
    async with SessionLocal() as s:
        # Check user
        r = await s.execute(select(User).where(User.username == 'tejasbhat2001@gmail.com'))
        u = r.scalar_one_or_none()
        if u:
            print(f"User found: {u.username}")
            print(f"ID: {u.id}")
            print(f"Is Admin: {u.is_admin}")
            print(f"Is Active: {u.is_active}")
            print(f"Requires Password Change: {u.requires_password_change}")
            print(f"TOTP Set Up: {u.totp_secret is not None}")
        else:
            print("User 'tejasbhat2001@gmail.com' not found.")
        
        # Check last logs
        r = await s.execute(select(AuthLog).order_by(AuthLog.created_at.desc()).limit(5))
        logs = r.scalars().all()
        print("\nLast 5 Auth Logs:")
        for log in logs:
            print(f"{log.created_at} | UserID: {log.user_id} | Event: {log.event_type} | IP: {log.ip_address}")

if __name__ == "__main__":
    asyncio.run(check_user())
