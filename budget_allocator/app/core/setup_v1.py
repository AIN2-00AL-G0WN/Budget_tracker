"""
app/core/setup_v1.py
--------------------
V1 Bootstrapper: Pre-populates baseline projects and rate cards on empty initializations.
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Family, RateCard

logger = logging.getLogger(__name__)

SEED_PROJECTS = [
    {"business_unit": "CPE", "name": "INKJET"},
    {"business_unit": "CPE", "name": "LASERJET"},
    {"business_unit": "NPI", "name": "INKJET"},
    {"business_unit": "NPI", "name": "LASERJET"},
    {"business_unit": "CSS", "name": "INKJET"},
    {"business_unit": "CSS", "name": "LASERJET"},
]

SEED_RATE_CARDS = [
    {"key_name": "manual_tc_multiplier", "value": 0.8},
    {"key_name": "automation_tc_multiplier", "value": 0.2},
    {"key_name": "adhoc_request_multiplier", "value": 0.2},
    {"key_name": "working_days_per_week", "value": 5.0},
    {"key_name": "hrs_per_wk_per_hc", "value": 40.0},
    {"key_name": "manual_hc_divisor", "value": 3.5},
    {"key_name": "automation_hc_divisor", "value": 5.0},
    {"key_name": "hc_rate_card", "value": 2.0},
    {"key_name": "sqpm_boise_pct", "value": 0.7},
    {"key_name": "pl_pct", "value": 0.5},
    {"key_name": "per_wqe_pct", "value": 0.4},
    {"key_name": "asqpm_pct", "value": 0.8},
    {"key_name": "lab_tech_manager_pct", "value": 0.4},
    {"key_name": "project_manager_pct", "value": 0.4},
]


async def seed_initial_data(db: AsyncSession) -> None:
    from app.models.models import BusinessUnit
    
    # 0. Extract unique Business Units and seed them
    unique_bus = list(set(proj["business_unit"] for proj in SEED_PROJECTS))
    bu_id_map = {}
    for bu_name in unique_bus:
        result = await db.execute(select(BusinessUnit).where(BusinessUnit.name == bu_name))
        existing_bu = result.scalar_one_or_none()
        
        if existing_bu is None:
            logger.info("Seeding new Business Unit: %s", bu_name)
            new_bu = BusinessUnit(name=bu_name)
            db.add(new_bu)
            await db.flush()
            bu_id_map[bu_name] = new_bu.id
        else:
            if existing_bu.is_deleted:
                logger.info("Reactivating soft-deleted Business Unit: %s", bu_name)
                existing_bu.is_deleted = False
                db.add(existing_bu)
                await db.flush()
            bu_id_map[bu_name] = existing_bu.id

    # 1. Seed each required project — reactivate if soft-deleted, create if missing entirely
    for proj in SEED_PROJECTS:
        bu_id = bu_id_map[proj["business_unit"]]
        result = await db.execute(
            select(Family).where(
                Family.business_unit_id == bu_id,
                Family.name == proj["name"],
            ).limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            # Truly new — insert it
            logger.info("Seeding new family: [%s] %s", proj["business_unit"], proj["name"])
            db.add(Family(business_unit_id=bu_id, name=proj["name"], status="ACTIVE"))
        elif existing.is_deleted:
            # Soft-deleted — reactivate it
            logger.info("Reactivating soft-deleted family: [%s] %s", proj["business_unit"], proj["name"])
            existing.is_deleted = False
            existing.status = "ACTIVE"
            db.add(existing)
        # else: already active, nothing to do

    # 2. Seed each required rate card individually — only insert if that key is missing
    for rate in SEED_RATE_CARDS:
        existing_rate = await db.execute(
            select(RateCard).where(RateCard.key_name == rate["key_name"]).limit(1)
        )
        if not existing_rate.scalar_one_or_none():
            logger.info("Seeding rate card: %s", rate["key_name"])
            db.add(RateCard(
                key_name=rate["key_name"],
                value=rate["value"],
                description=f"Auto-generated V1 default for {rate['key_name']}"
            ))

    await db.commit()
    logger.info("V1 seeding check complete.")
