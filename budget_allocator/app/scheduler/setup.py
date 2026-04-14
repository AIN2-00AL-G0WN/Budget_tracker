"""
app/scheduler/setup.py
-----------------------
APScheduler integration with FastAPI's async lifespan.

Uses `AsyncIOScheduler` bound to the running event loop — no Celery,
no Redis, no external broker needed.  The scheduler itself is an
in-process background thread pool managed by APScheduler.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database import AsyncSessionLocal
from app.scheduler.jobs import check_deadline_proximity
from app.tasks.sweeper import sweep_expired_runs

logger = logging.getLogger(__name__)

# Module-level scheduler instance — imported by main.py lifespan
scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_deadline_check() -> None:
    """Wrapper that provides the DB session to the job coroutine."""
    async with AsyncSessionLocal() as session:
        try:
            await check_deadline_proximity(db=session)
        except Exception:
            logger.exception("Deadline check job failed")
            
async def _run_sweeper_check() -> None:
    try:
        await sweep_expired_runs()
    except Exception:
        logger.exception("Sweeper check job failed")


def configure_jobs() -> None:
    """Register all scheduled jobs."""
    scheduler.add_job(
        _run_deadline_check,
        trigger=CronTrigger(hour=0, minute=0),   # Daily at midnight UTC
        id="deadline_proximity_check",
        name="SubDivision deadline proximity alert",
        replace_existing=True,
        misfire_grace_time=3600,   # Allow up to 1h late execution on restart
    )
    scheduler.add_job(
        _run_sweeper_check,
        trigger=CronTrigger(hour=0, minute=0),
        id="sweeper_check",
        name="Mark Expired TestRuns as OVERDUE",
        replace_existing=True,
    )
    logger.info("Scheduled jobs configured")


@asynccontextmanager
async def scheduler_lifespan() -> AsyncGenerator[None, None]:
    """
    Async context manager for use inside FastAPI's lifespan.

    Usage in main.py::

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with scheduler_lifespan():
                yield
    """
    configure_jobs()
    scheduler.start()
    logger.info("APScheduler started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler shut down")
