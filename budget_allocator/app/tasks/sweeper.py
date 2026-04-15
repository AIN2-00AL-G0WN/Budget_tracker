import logging
import datetime
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.models import Run
from app.core.context import current_user_id, current_change_reason

logger = logging.getLogger(__name__)

async def sweep_expired_runs() -> None:
    """
    Background APScheduler job to mark expired TestRuns as OVERDUE.
    Triggers the SQLAlchemy event listener automatically via ORM commits.
    """
    logger.info("Starting nightly sweeper for expired TestRuns...")
    
    # SYSTEM actor context for the audit logger
    # In a real system, you might have a constant UUID for the SYSTEM actor.
    current_user_id.set(None)  # or uuid.UUID("system-uuid-here") if constrained
    current_change_reason.set("Automated System Action: Deadline reached.")

    today = datetime.date.today()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Run).where(
                Run.end_date < today,
                Run.status.notin_(["CLOSED", "OVERDUE"])
            )
        )
        expired_runs = result.scalars().all()
        
        if not expired_runs:
            logger.info("No expired Runs found to sweep.")
            return

        for run in expired_runs:
            run.status = "OVERDUE"
            session.add(run)
            
        await session.commit()
        logger.info(f"Sweeper finished successfully. Marked {len(expired_runs)} runs OVERDUE.")
