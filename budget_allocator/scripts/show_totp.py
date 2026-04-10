"""Show current TOTP state for all users."""
import asyncio, sys, os, io

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models.models import User
import pyotp


async def main():
    engine = create_async_engine(settings.database_url, echo=False)
    AS = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with AS() as s:
        result = await s.execute(select(User).where(User.is_active == True))
        users = result.scalars().all()
        for u in users:
            print("=" * 60)
            print(f"  Username:    {u.username}")
            print(f"  Token Ver:   {u.token_version}")
            print(f"  PW Change:   {u.requires_password_change}")
            if u.totp_secret:
                t = pyotp.TOTP(u.totp_secret)
                print(f"  TOTP Secret: {u.totp_secret}")
                print(f"  Server Code: {t.now()}")
                uri = t.provisioning_uri(name=u.username, issuer_name="BudgetAllocator")
                print(f"  Scan URI:    {uri}")
            else:
                print(f"  TOTP: NOT SET")
            print()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
