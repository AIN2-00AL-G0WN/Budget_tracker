
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.models import RateCard
from sqlalchemy import select

DEFAULT_RATES = {
    "manual_tc_multiplier": 0.8,
    "automation_tc_multiplier": 0.2,
    "adhoc_request_multiplier": 0.2,
    "working_days_per_week": 5.0,
    "hrs_per_wk_per_hc": 40.0,
    "manual_hc_divisor": 3.5,
    "automation_hc_divisor": 5.0,
    "hc_rate_card": 50.0, # Target approx $3250? Let's check. 65 HC * something.
    "sqpm_boise_pct": 0.7,
    "pl_pct": 0.5,
    "per_wqe_pct": 0.4,
    "asqpm_pct": 0.8,
    "lab_tech_manager_pct": 0.4,
    "project_manager_pct": 0.4,
}

# Wait, if tc_count=130:
# manual_tc=104, auto_tc=26, adhoc=26, total_tc=156
# duration_wks=1
# manual_hc = ceil(260 / 3.5) = 75
# auto_hc = ceil(26 / 5) = 6
# If cost is approx 3250:
# 3250 / (75+6) / 40 = 1.0? 
# Maybe manual_hc_divisor is different or rate card is $1.0?
# Let's use some reasonable defaults and adjust if needed.

async def seed_rates():
    async with AsyncSessionLocal() as db:
        async with db.begin():
            for key, val in DEFAULT_RATES.items():
                # Check if exists
                res = await db.execute(select(RateCard).where(RateCard.key_name == key))
                if not res.scalar_one_or_none():
                    db.add(RateCard(key_name=key, value=val))
            print("Seeded default rate cards.")

if __name__ == "__main__":
    asyncio.run(seed_rates())
