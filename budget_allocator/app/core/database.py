"""
app/core/database.py
--------------------
Async SQLAlchemy 2.0 engine + session factory.

All database I/O in this project MUST go through the async `AsyncSession`.
The `get_db` FastAPI dependency is defined here so that every request gets
its own properly-scoped session that is automatically closed on teardown.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Shared declarative base for every SQLAlchemy model in this project.

    All models must inherit from this class so that Alembic's auto-generation
    (`alembic revision --autogenerate`) can detect them automatically.
    """
    pass


# ---------------------------------------------------------------------------
# Async Engine
# ---------------------------------------------------------------------------

def _build_engine() -> AsyncEngine:
    """
    Create and configure the async SQLAlchemy engine.

    Pool settings are conservative for an internal tool running on a single
    server.  Adjust `pool_size` / `max_overflow` if you scale out later.
    """
    return create_async_engine(
        settings.database_url,          # e.g. "postgresql+asyncpg://user:pw@host/db"
        echo=settings.db_echo,          # Set False in production
        pool_pre_ping=True,             # Detect stale connections automatically
        pool_size=10,
        max_overflow=20,
    )


engine: AsyncEngine = _build_engine()


# ---------------------------------------------------------------------------
# Session Factory
# ---------------------------------------------------------------------------

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Avoid lazy-load errors after commit in async code
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# FastAPI Dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an `AsyncSession` for the duration of a single HTTP request.

    Usage inside a router::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...

    The session is committed on success and rolled back on any unhandled
    exception, then closed regardless.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
