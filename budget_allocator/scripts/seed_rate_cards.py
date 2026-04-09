"""
scripts/seed_rate_cards.py
---------------------------
One-shot seeder: inserts the default RateCard rows that the
CalculationService requires into the database.

Run once after the first `alembic upgrade head`:

    python -m scripts.seed_rate_cards

Idempotent — existing records are updated, new ones are inserted.
"""

from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import AsyncSessionLocal
from app.models.models import RateCard

DEFAULT_RATES: list[dict] = [
    {"key_name": "manual_tc_multiplier",     "value": 0.8,   "description": "Share of TCs handled manually (=C2*0.8)"},
    {"key_name": "automation_tc_multiplier", "value": 0.2,   "description": "Share of TCs automated (=C2*0.2)"},
    {"key_name": "adhoc_request_multiplier", "value": 0.2,   "description": "Adhoc request TC ratio (=C2*0.2)"},
    {"key_name": "working_days_per_week",    "value": 5.0,   "description": "Working days per week (divisor for wks calc)"},
    {"key_name": "hrs_per_wk_per_hc",       "value": 40.0,  "description": "Billable hours per head per week (40hr rate card)"},
    {"key_name": "manual_hc_divisor",       "value": 3.5,   "description": "Denominator in Manual HC formula (/C10/3.5)"},
    {"key_name": "automation_hc_divisor",   "value": 5.0,   "description": "Divisor for Automation HC (/5)"},
    {"key_name": "hc_rate_card",            "value": 2.00,  "description": "$/hr equivalent rate card multiplier"},
    {"key_name": "sqpm_boise_pct",          "value": 0.7,   "description": "SQPM Cost of Boise 70%"},
    {"key_name": "pl_pct",                  "value": 0.5,   "description": "PL-50%"},
    {"key_name": "per_wqe_pct",             "value": 0.4,   "description": "Per WQE - 40% (applied to 6 WQE resources)"},
    {"key_name": "asqpm_pct",               "value": 0.8,   "description": "aSQPM - 80%"},
    {"key_name": "lab_tech_manager_pct",    "value": 0.4,   "description": "Lab Tech & Manager - 40% (applied to 2 resources)"},
    {"key_name": "project_manager_pct",     "value": 0.4,   "description": "Project Manager - 40%"},
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        for entry in DEFAULT_RATES:
            result = await session.execute(
                select(RateCard).where(RateCard.key_name == entry["key_name"])
            )
            existing: RateCard | None = result.scalar_one_or_none()
            if existing:
                existing.value = entry["value"]
                existing.description = entry["description"]
                session.add(existing)
                print(f"  UPDATED  {entry['key_name']} = {entry['value']}")
            else:
                session.add(RateCard(**entry))
                print(f"  INSERTED {entry['key_name']} = {entry['value']}")
        await session.commit()
    print("\n✅  Rate cards seeded successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
