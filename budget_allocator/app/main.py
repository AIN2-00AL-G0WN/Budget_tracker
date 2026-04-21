"""
app/main.py
-----------
FastAPI application factory and entry point.

Startup sequence
----------------
1. Register SQLAlchemy audit event listeners.
2. Start APScheduler (binds to the running asyncio event loop).
3. Mount all API routers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.business_units import router as business_units_router
from app.api.v1.endpoints.families import router as families_router
from app.api.v1.endpoints.teams import router as teams_router
from app.api.v1.endpoints.runs import router as runs_router
from app.api.v1.endpoints.budgets import router as budgets_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.meta import router as meta_router
from app.api.v1.endpoints.analytics import router as analytics_router
from app.api.v1.endpoints.lookups import router as lookups_router
from app.core.config import settings
from app.scheduler.setup import scheduler_lifespan
from app.services.audit_logger import register_audit_listeners

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Code that runs on startup (before `yield`) and shutdown (after `yield`).
    """
    # 1. Wire up automatic audit logging
    register_audit_listeners()

    # (Database Seeding is handled via scripts/init_db.py before startup)
    
    # 2. Start the in-process scheduler
    async with scheduler_lifespan():
        logger.info("🚀  %s v%s is ready", settings.app_name, settings.app_version)
        yield

    logger.info("👋  Application shutting down")


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Internal Budget Allocator & Tracker — secure, offline-capable, "
        "hybrid calculation engine backed by PostgreSQL."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — restrict to your internal frontend origin in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # ← Replace with specific origin(s) before deploying
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

API_V1_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=API_V1_PREFIX)
app.include_router(business_units_router, prefix=API_V1_PREFIX)
app.include_router(families_router, prefix=API_V1_PREFIX)
app.include_router(teams_router, prefix=API_V1_PREFIX)
app.include_router(runs_router, prefix=API_V1_PREFIX)
app.include_router(budgets_router, prefix=API_V1_PREFIX)
app.include_router(admin_router, prefix=API_V1_PREFIX)
app.include_router(meta_router, prefix=API_V1_PREFIX)
app.include_router(analytics_router, prefix=API_V1_PREFIX)
app.include_router(lookups_router, prefix=API_V1_PREFIX)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "version": settings.app_version}
