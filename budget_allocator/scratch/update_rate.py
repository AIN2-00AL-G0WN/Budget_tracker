
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.models import RateCard
from sqlalchemy import update

async def update_rate():
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(update(RateCard).where(RateCard.key_name == "hc_rate_card").values(value=1.0))
            print("Updated hc_rate_card to 1.0")

if __name__ == "__main__":
    asyncio.run(update_rate())
