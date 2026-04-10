import asyncio
import os
from sqlalchemy import select
from app.core.database import get_db
from app.models.models import User

async def main():
    async for db in get_db():
        result = await db.execute(select(User))
        users = result.scalars().all()
        print("User Check:")
        for u in users:
            print(f"- Username: {u.username}")
            print(f"  Is Active: {u.is_active}")
            print(f"  TOTP Enabled: {u.totp_secret is not None}")
        break

if __name__ == "__main__":
    asyncio.run(main())
