"""
scripts/test_multi_goal_seek.py
-------------------------------
Verification script for the Multi-Target Goal-Seek Solver.
"""

import asyncio
import io
import os
import sys

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make sure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.models import Budget, BusinessUnit, Family, Team, Run
from app.services.goal_seek_service import solve_multi_goal_seek
from app.services.calculation_service import fetch_rate_cards


async def verify_multi_goal_seek():
    # Database Session Initialization
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("🔍 Checking for existing budgets in the database...")
        stmt = select(Budget).limit(1)
        res = await session.execute(stmt)
        budget = res.scalar_one_or_none()

        created_dummy = False
        if not budget:
            print("⚠️ No budgets found. Creating a self-contained dummy structure for testing...")
            bu = BusinessUnit(name="Test BU Solver Multi")
            session.add(bu)
            await session.flush()

            fam = Family(name="Test Family Solver Multi", business_unit_id=bu.id)
            session.add(fam)
            await session.flush()

            team = Team(name="Test Team Solver Multi", family_id=fam.id)
            session.add(team)
            await session.flush()

            run = Run(name="Test Run Solver Multi", team_id=team.id)
            session.add(run)
            await session.flush()

            budget = Budget(
                run_id=run.id,
                tc_count=100.0,
                duration_in_days=10.0,
                manual_tc_count=80.0,
                automation_tc_count=20.0,
                adhoc_request=20.0,
                total_tc=120.0,
                duration_wks=2.0,
                manual_hc=120.0 / (10.0 * 3.5),
                automation_hc=20.0 / 5.0,
                manual_hc_cost=1000.0,
                automation_hc_cost=400.0,
                lead_cost=160.0,
                direct_hc_cost=1560.0,
                sqpm_cost_boise=112.0,
                pl_cost=80.0,
                per_wqe_cost=384.0,
                asqpm_cost=128.0,
                lab_tech_manager_cost=128.0,
                project_manager_cost=64.0,
                total_budget=2456.0,
                indirect_hc_cost=896.0,
            )
            session.add(budget)
            await session.flush()
            created_dummy = True
            print("✅ Dummy budget created successfully.")

        print(f"📊 Baseline Budget ID: {budget.id}")
        print(f"  Total Budget: ${budget.total_budget:,.2f}")
        print(f"  ASQPM Cost: ${budget.asqpm_cost:,.2f}")
        print(f"  Manual Cost: ${budget.manual_hc_cost:,.2f}")

        # -------------------------------------------------------------
        # Test Case 1: Independent Targets (asqpm_cost = $500, manual_hc_cost = $480)
        # We adjust rates for asqpm and manual, keeping everything else locked.
        # -------------------------------------------------------------
        targets1 = {
            "asqpm_cost": 500.00,
            "manual_hc_cost": 480.00
        }
        knobs1 = ["asqpm_hourly_rate_override", "manual_hourly_rate_override"]
        
        print(f"\n🎯 Test 1: Independent Targets (ASQPM Cost = $500, Manual Cost = $480)")
        print(f"    Adjusting Knobs: {knobs1}")
        
        res1 = await solve_multi_goal_seek(
            db=session,
            budget=budget,
            targets=targets1,
            adjustable_knobs=knobs1
        )

        print(f"    Solved values:")
        for k, v in res1["adjustments"].items():
            print(f"        - {k}: {v:.4f}")
        print(f"    Resulting values:")
        for k, v in res1["resulting_values"].items():
            print(f"        - {k}: ${v:.2f}")
        print(f"    Warning: {res1['warning']}")

        # Verify no warning and exact matching
        assert res1["warning"] is None, "Test 1 failed: Solver should not warning on exact solutions."
        for field, target_val in targets1.items():
            diff = abs(res1["resulting_values"][field] - target_val)
            assert diff < 0.05, f"Test 1 failed: {field} value {res1['resulting_values'][field]} deviates too much from target {target_val}"
        print("    ✅ Test 1 Verified: Solved exactly without conflicts!")

        # -------------------------------------------------------------
        # Test Case 2: Conflicting Targets (asqpm_cost = $500, lead_cost = $480)
        # We ONLY adjust duration_in_days (which governs both linearly with a fixed ratio).
        # This is mathematically impossible to solve exactly. The solver must find a compromise.
        # -------------------------------------------------------------
        targets2 = {
            "asqpm_cost": 500.00,
            "lead_cost": 480.00
        }
        knobs2 = ["duration_in_days"]

        print(f"\n🎯 Test 2: Conflicting Targets (ASQPM Cost = $500, Lead Cost = $480)")
        print(f"    Adjusting Knobs: {knobs2} (only duration)")

        res2 = await solve_multi_goal_seek(
            db=session,
            budget=budget,
            targets=targets2,
            adjustable_knobs=knobs2
        )

        print(f"    Solved values:")
        for k, v in res2["adjustments"].items():
            print(f"        - {k}: {v:.4f} days")
        print(f"    Resulting values:")
        for k, v in res2["resulting_values"].items():
            print(f"        - {k}: ${v:.2f} (Target: ${targets2[k]:.2f})")
        print(f"    Warning: {res2['warning']}")

        # Verify warning is generated and compromise values exist
        assert res2["warning"] is not None, "Test 2 failed: Solver should have warned on conflicting constraints."
        print("    ✅ Test 2 Verified: Solver successfully found the compromise and raised a warning!")

        if created_dummy:
            print("\n🧹 Rolling back transaction...")
            await session.rollback()
        else:
            await session.commit()

    await engine.dispose()
    print("\n🎉 Multi-Target Goal-Seek verification successfully complete!")


if __name__ == "__main__":
    asyncio.run(verify_multi_goal_seek())
