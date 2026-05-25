"""
scripts/test_goal_seek.py
-------------------------
Verification script for the Goal-Seek Solver.
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
from app.services.goal_seek_service import solve_goal_seek
from app.services.calculation_service import fetch_rate_cards


async def verify_goal_seek():
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
            # We need BusinessUnit -> Family -> Team -> Run -> Budget
            bu = BusinessUnit(name="Test BU Solver")
            session.add(bu)
            await session.flush()

            fam = Family(name="Test Family Solver", business_unit_id=bu.id)
            session.add(fam)
            await session.flush()

            team = Team(name="Test Team Solver", family_id=fam.id)
            session.add(team)
            await session.flush()

            run = Run(name="Test Run Solver", team_id=team.id)
            session.add(run)
            await session.flush()

            # Insert baseline rate cards if not present
            rates = await fetch_rate_cards(session)

            budget = Budget(
                run_id=run.id,
                tc_count=100.0,
                duration_in_days=10.0,
                manual_tc_count=80.0,
                automation_tc_count=20.0,
                adhoc_request=20.0,
                total_tc=120.0,
                duration_wks=2.0,
                manual_hc=120.0 / (10.0 * 3.5), # ~3.428
                automation_hc=20.0 / 5.0, # 4.0
                # Cost math
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

        # Unconditionally fetch rates and define evaluate_parameter helper
        rates = await fetch_rate_cards(session)
        baseline_overrides = {
            "manual_tc_multiplier_override": budget.manual_tc_multiplier_override,
            "automation_tc_multiplier_override": budget.automation_tc_multiplier_override,
            "adhoc_request_multiplier_override": budget.adhoc_request_multiplier_override,
            "working_days_per_week_override": budget.working_days_per_week_override,
            "hrs_per_wk_per_hc_override": budget.hrs_per_wk_per_hc_override,
            "manual_hc_divisor_override": budget.manual_hc_divisor_override,
            "automation_hc_divisor_override": budget.automation_hc_divisor_override,
            "manual_hourly_rate_override": budget.manual_hourly_rate_override,
            "automation_hourly_rate_override": budget.automation_hourly_rate_override,
            "asqpm_hourly_rate_override": budget.asqpm_hourly_rate_override,
            "lead_hourly_rate_override": budget.lead_hourly_rate_override,
            "pm_hourly_rate_override": budget.pm_hourly_rate_override,
            "sqpm_boise_pct_override": budget.sqpm_boise_pct_override,
            "pl_pct_override": budget.pl_pct_override,
            "per_wqe_pct_override": budget.per_wqe_pct_override,
            "asqpm_pct_override": budget.asqpm_pct_override,
            "lab_tech_manager_pct_override": budget.lab_tech_manager_pct_override,
            "project_manager_pct_override": budget.project_manager_pct_override,
        }
        baseline_overrides = {k: v for k, v in baseline_overrides.items() if v is not None}

        def evaluate_parameter(knob: str, value: float) -> dict:
            from app.services.calculation_service import calculate_budget
            if knob == "tc_count":
                return calculate_budget(
                    tc_count=value,
                    duration_in_days=budget.duration_in_days,
                    rates=rates,
                    overrides=baseline_overrides,
                )
            elif knob == "duration_in_days":
                return calculate_budget(
                    tc_count=budget.tc_count,
                    duration_in_days=value,
                    rates=rates,
                    overrides=baseline_overrides,
                )
            else:
                temp_overrides = baseline_overrides.copy()
                temp_overrides[knob] = value
                return calculate_budget(
                    tc_count=budget.tc_count,
                    duration_in_days=budget.duration_in_days,
                    rates=rates,
                    overrides=temp_overrides,
                )

        print(f"📊 Baseline Budget ID: {budget.id}")
        print(f"  TC Count: {budget.tc_count}")
        print(f"  Duration (Days): {budget.duration_in_days}")
        print(f"  Total Budget: ${budget.total_budget:,.2f}")

        # -------------------------------------------------------------
        # Test Case 1: Target total_budget = $3,000.00
        # -------------------------------------------------------------
        target_field = "total_budget"
        target_value = 3000.00
        print(f"\n🎯 Running Goal Seek targeting {target_field} = ${target_value:,.2f}...")
        
        results = await solve_goal_seek(
            db=session,
            budget=budget,
            target_field=target_field,
            target_value=target_value
        )

        print(f"Current Value: ${results['current_value']:,.2f}")
        print(f"Suggestions Found: {len(results['suggestions'])}")

        for sugg in results["suggestions"]:
            print(f"\n  [{sugg['rank']}] Suggestion: {sugg['name']}")
            print(f"      Description: {sugg['description']}")
            action = sugg["proposed_action"]
            print(f"      Modify Knob: '{action['parameter']}'")
            print(f"      Change: {action['current_value']} ➔ {action['new_value']:.4f}")
            print(f"      Side-effects count: {sugg['side_effects_count']}")
            print(f"      Impact Diff Map:")
            for field, diff_str in list(sugg["impact"].items())[:5]: # Print first 5
                print(f"          - {field}: {diff_str}")

            # Verification step: run evaluate_parameter to verify it hits the target!
            verified_res = evaluate_parameter(action["parameter"], action["new_value"])
            actual_target_val = verified_res[target_field]
            deviation = abs(actual_target_val - target_value)
            print(f"      Verification: Calculated value = ${actual_target_val:,.2f} (deviation = ${deviation:.4f})")
            assert deviation < 0.05, f"Goal seek failed for knob {action['parameter']}. Expected {target_value}, got {actual_target_val}"
            print("      ✅ Solved value verified successfully!")

        # -------------------------------------------------------------
        # Test Case 2: Target automation_hc_cost = $600.00
        # -------------------------------------------------------------
        target_field2 = "automation_hc_cost"
        target_value2 = 600.00
        print(f"\n🎯 Running Goal Seek targeting {target_field2} = ${target_value2:,.2f}...")

        results2 = await solve_goal_seek(
            db=session,
            budget=budget,
            target_field=target_field2,
            target_value=target_value2
        )

        print(f"Current Value: ${results2['current_value']:,.2f}")
        print(f"Suggestions Found: {len(results2['suggestions'])}")

        for sugg in results2["suggestions"]:
            print(f"\n  [{sugg['rank']}] Suggestion: {sugg['name']}")
            print(f"      Modify Knob: '{sugg['proposed_action']['parameter']}'")
            print(f"      Change: {sugg['proposed_action']['current_value']} ➔ {sugg['proposed_action']['new_value']:.4f}")
            print(f"      Side-effects count: {sugg['side_effects_count']}")

            # Verification
            action = sugg["proposed_action"]
            verified_res = evaluate_parameter(action["parameter"], action["new_value"])
            actual_target_val = verified_res[target_field2]
            deviation = abs(actual_target_val - target_value2)
            print(f"      Verification: Calculated value = ${actual_target_val:,.2f} (deviation = ${deviation:.4f})")
            assert deviation < 0.05, f"Goal seek failed for knob {action['parameter']}. Expected {target_value2}, got {actual_target_val}"
            print("      ✅ Solved value verified successfully!")

        if created_dummy:
            print("\n🧹 Rolling back transaction to keep database clean...")
            await session.rollback()
        else:
            await session.commit()

    await engine.dispose()
    print("\n🎉 Goal-Seek verification successfully complete!")


if __name__ == "__main__":
    asyncio.run(verify_goal_seek())
