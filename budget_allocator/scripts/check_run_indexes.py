import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = "postgresql+asyncpg://postgres:12345@localhost:5432/budgetTracker"

async def check_indexes():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'runs';"))
        for row in res.fetchall():
            print(f"Index: {row[0]}, Def: {row[1]}")
    await engine.dispose()

asyncio.run(check_indexes())
