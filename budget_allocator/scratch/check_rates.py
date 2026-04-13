
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.models import RateCard
from sqlalchemy import select

async def check_rates():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(RateCard))
        rates = result.scalars().all()
        for r in rates:
            print(f"{r.key_name}: {r.value}")

if __name__ == "__main__":
    asyncio.run(check_rates())
