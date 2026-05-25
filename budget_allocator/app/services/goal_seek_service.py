"""
app/services/goal_seek_service.py
---------------------------------
Goal-Seek & Sensitivity Solver Engine.

Design Notes:
* treat the existing budget calculation engine as a black-box forward model.
* Dynamic Dependency Discovery: Perturbs variables (knobs) to dynamically discover
  which ones affect the target field (finite differences).
* Bisection Search Solver: Iteratively solves for target values in milliseconds.
* Side-Effect Diff Engine: Identifies all changes in other calculated columns.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget
from app.services.calculation_service import calculate_budget, fetch_rate_cards

logger = logging.getLogger(__name__)

# List of all knobs that the manager can adjust to reach their target.
ADJUSTABLE_KNOBS = [
    "tc_count",
    "duration_in_days",
    "manual_tc_multiplier_override",
    "automation_tc_multiplier_override",
    "adhoc_request_multiplier_override",
    "working_days_per_week_override",
    "hrs_per_wk_per_hc_override",
    "manual_hc_divisor_override",
    "automation_hc_divisor_override",
    "manual_hourly_rate_override",
    "automation_hourly_rate_override",
    "asqpm_hourly_rate_override",
    "lead_hourly_rate_override",
    "pm_hourly_rate_override",
    "sqpm_boise_pct_override",
    "pl_pct_override",
    "per_wqe_pct_override",
    "asqpm_pct_override",
    "lab_tech_manager_pct_override",
    "project_manager_pct_override",
]

# Friendly names and descriptions for Pydantic suggestions
KNOB_METADATA = {
    "tc_count": {
        "name": "Test Case Count",
        "description": "Adjust the baseline test case count. This is a core input and affects resource scaling across all departments.",
    },
    "duration_in_days": {
        "name": "Engagement Duration (Days)",
        "description": "Adjust the duration of the engagement. This affects daily rate costs and time-based resource lines (like leads and PMs).",
    },
    "manual_tc_multiplier_override": {
        "name": "Manual Test Case Multiplier Override",
        "description": "Override the percentage of test cases categorized as manual.",
    },
    "automation_tc_multiplier_override": {
        "name": "Automation Test Case Multiplier Override",
        "description": "Override the percentage of test cases categorized as automated.",
    },
    "adhoc_request_multiplier_override": {
        "name": "Adhoc Request Multiplier Override",
        "description": "Override the percentage of test cases added for adhoc requests.",
    },
    "working_days_per_week_override": {
        "name": "Working Days Per Week Override",
        "description": "Override the number of business working days in a week.",
    },
    "hrs_per_wk_per_hc_override": {
        "name": "Hours Per Week Per Headcount Override",
        "description": "Override the standard weekly hours expected per headcount.",
    },
    "manual_hc_divisor_override": {
        "name": "Manual Headcount Divisor Override",
        "description": "Override the divisor parameter in the manual headcount formula.",
    },
    "automation_hc_divisor_override": {
        "name": "Automation Headcount Divisor Override",
        "description": "Override the divisor parameter in the automation headcount formula.",
    },
    "manual_hourly_rate_override": {
        "name": "Manual Hourly Rate Override",
        "description": "Override the contract hourly rate for manual testing resources.",
    },
    "automation_hourly_rate_override": {
        "name": "Automation Hourly Rate Override",
        "description": "Override the contract hourly rate for automation engineering resources.",
    },
    "asqpm_hourly_rate_override": {
        "name": "aSQPM Hourly Rate Override",
        "description": "Override the contract hourly rate for auxiliary SQPM resources.",
    },
    "lead_hourly_rate_override": {
        "name": "Lead Hourly Rate Override",
        "description": "Override the contract hourly rate for Test Lead resources.",
    },
    "pm_hourly_rate_override": {
        "name": "Project Manager Hourly Rate Override",
        "description": "Override the contract hourly rate for Project Managers.",
    },
    "sqpm_boise_pct_override": {
        "name": "SQPM Boise Percentage Allocation Override",
        "description": "Override the percentage allocation factor for Boise SQPM resources.",
    },
    "pl_pct_override": {
        "name": "Project Lead Percentage Allocation Override",
        "description": "Override the percentage allocation factor for Project Leads.",
    },
    "per_wqe_pct_override": {
        "name": "WQE Percentage Allocation Override",
        "description": "Override the percentage allocation factor for WQE resources.",
    },
    "asqpm_pct_override": {
        "name": "aSQPM Percentage Allocation Override",
        "description": "Override the percentage allocation factor for auxiliary SQPMs.",
    },
    "lab_tech_manager_pct_override": {
        "name": "Lab Tech & Manager Allocation Override",
        "description": "Override the percentage allocation factor for Lab Techs and Managers.",
    },
    "project_manager_pct_override": {
        "name": "Project Manager Allocation Override",
        "description": "Override the percentage allocation factor for Project Managers.",
    }
}

OVERRIDE_TO_GLOBAL_KEY = {
    "manual_tc_multiplier_override": "manual_tc_multiplier",
    "automation_tc_multiplier_override": "automation_tc_multiplier",
    "adhoc_request_multiplier_override": "adhoc_request_multiplier",
    "working_days_per_week_override": "working_days_per_week",
    "hrs_per_wk_per_hc_override": "hrs_per_wk_per_hc",
    "manual_hc_divisor_override": "manual_hc_divisor",
    "automation_hc_divisor_override": "automation_hc_divisor",
    "manual_hourly_rate_override": "hc_rate_card",
    "automation_hourly_rate_override": "hc_rate_card",
    "asqpm_hourly_rate_override": "hc_rate_card",
    "lead_hourly_rate_override": "hc_rate_card",
    "pm_hourly_rate_override": "hc_rate_card",
    "sqpm_boise_pct_override": "sqpm_boise_pct",
    "pl_pct_override": "pl_pct",
    "per_wqe_pct_override": "per_wqe_pct",
    "asqpm_pct_override": "asqpm_pct",
    "lab_tech_manager_pct_override": "lab_tech_manager_pct",
    "project_manager_pct_override": "project_manager_pct",
}

COST_FIELDS = {
    "manual_hc_cost",
    "automation_hc_cost",
    "lead_cost",
    "sqpm_cost_boise",
    "pl_cost",
    "per_wqe_cost",
    "asqpm_cost",
    "lab_tech_manager_cost",
    "project_manager_cost",
    "direct_hc_cost",
    "indirect_hc_cost",
    "total_budget"
}


def get_search_bounds(param_name: str, current_val: float) -> tuple[float, float]:
    """Return the search bounds [low, high] for bisection search based on the knob type."""
    if param_name.endswith("_pct_override"):
        return 0.0, 1.0
    elif "hourly_rate" in param_name:
        return 0.0, 5000.0  # Max rate of $5,000/hr
    elif "multiplier" in param_name:
        return 0.0, 10.0
    elif "divisor" in param_name:
        return 0.05, 100.0  # Avoid zero divisor
    elif param_name == "tc_count":
        return 1.0, 10000000.0
    elif param_name == "duration_in_days":
        return 1.0, 10000.0
    elif param_name == "working_days_per_week_override":
        return 1.0, 7.0
    elif param_name == "hrs_per_wk_per_hc_override":
        return 1.0, 168.0
    else:
        return 0.0, max(100.0, current_val * 10.0)


async def solve_goal_seek(
    db: AsyncSession,
    budget: Budget,
    target_field: str,
    target_value: float
) -> dict[str, Any]:
    """
    Solves for target_value on target_field across all adjustable parameters
    and compiles ranked suggestions with side-effects diffs.
    """
    rates = await fetch_rate_cards(db)

    # 1. Capture current baseline values from the budget row
    baseline_tc_count = budget.tc_count
    baseline_duration = budget.duration_in_days

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
    # Keep only active overrides
    baseline_overrides = {k: v for k, v in baseline_overrides.items() if v is not None}

    # 2. Run the baseline calculation
    baseline_result = calculate_budget(
        tc_count=baseline_tc_count,
        duration_in_days=baseline_duration,
        rates=rates,
        overrides=baseline_overrides,
    )

    if target_field not in baseline_result:
        raise ValueError(
            f"We couldn't find a calculated budget field named '{target_field}'. "
            "Please verify that the name matches one of the columns on your budget worksheet."
        )

    current_value = baseline_result[target_field]

    # Helper function to evaluate the calculation for a single knob modification
    def evaluate_parameter(knob: str, value: float) -> dict[str, Any]:
        if knob == "tc_count":
            return calculate_budget(
                tc_count=value,
                duration_in_days=baseline_duration,
                rates=rates,
                overrides=baseline_overrides,
            )
        elif knob == "duration_in_days":
            return calculate_budget(
                tc_count=baseline_tc_count,
                duration_in_days=value,
                rates=rates,
                overrides=baseline_overrides,
            )
        else:
            temp_overrides = baseline_overrides.copy()
            temp_overrides[knob] = value
            return calculate_budget(
                tc_count=baseline_tc_count,
                duration_in_days=baseline_duration,
                rates=rates,
                overrides=temp_overrides,
            )

    suggestions = []

    # 3. Iterate through each knob to verify dependency and run Bisection Search
    for knob in ADJUSTABLE_KNOBS:
        # Determine the current effective value of this knob
        if knob == "tc_count":
            cur_effective_val = baseline_tc_count
        elif knob == "duration_in_days":
            cur_effective_val = baseline_duration
        else:
            # If set on the budget, use it. Otherwise, look up the global rate.
            cur_effective_val = baseline_overrides.get(knob)
            if cur_effective_val is None:
                global_key = OVERRIDE_TO_GLOBAL_KEY[knob]
                cur_effective_val = rates[global_key]

        # A. Dynamic Dependency Discovery (Numerical Perturbation)
        epsilon = 0.001 if cur_effective_val == 0 else cur_effective_val * 0.005
        # Ensure epsilon is safe for divisors
        if knob.endswith("divisor_override") and cur_effective_val + epsilon <= 0:
            epsilon = 0.1

        perturbed_res = evaluate_parameter(knob, cur_effective_val + epsilon)
        diff = abs(perturbed_res[target_field] - current_value)

        # If perturbed output does not change, this knob is independent of the target field
        if diff < 1e-6:
            continue

        # B. Bisection Search Solver
        low, high = get_search_bounds(knob, cur_effective_val)
        
        try:
            val_at_low = evaluate_parameter(knob, low)[target_field]
            val_at_high = evaluate_parameter(knob, high)[target_field]
        except ValueError:
            # Divisor override zero guard or invalid inputs
            continue

        # Target value must be within the search bounds
        if not (min(val_at_low, val_at_high) <= target_value <= max(val_at_low, val_at_high)):
            continue

        solved_val = None
        for _ in range(50):  # Bisection iterations
            mid = (low + high) / 2.0
            try:
                val_at_mid = evaluate_parameter(knob, mid)[target_field]
            except ValueError:
                # Bisection split hit a division-by-zero boundary
                break

            if abs(val_at_mid - target_value) < 0.005:  # Half-cent precision matches DB scale
                solved_val = mid
                break

            if val_at_high > val_at_low:  # Monotonically increasing
                if val_at_mid < target_value:
                    low = mid
                else:
                    high = mid
            else:  # Monotonically decreasing
                if val_at_mid < target_value:
                    high = mid
                else:
                    low = mid
        else:
            solved_val = (low + high) / 2.0

        if solved_val is None:
            continue

        # C. Side-Effect Diff Engine
        new_result = evaluate_parameter(knob, solved_val)
        
        impact = {}
        side_effects_count = 0
        for field, new_val in new_result.items():
            # Skip the knob itself and baseline fields that are overrides we didn't adjust
            if field == knob or field.endswith("_override") or field in {"run_id", "id"}:
                continue
                
            old_val = baseline_result.get(field)
            if old_val is None:
                continue

            diff_val = new_val - old_val
            if abs(diff_val) > 0.005:  # Changed fields
                if field != target_field:
                    side_effects_count += 1
                
                # Format output nicely based on type (Cost vs Count)
                sign = "+" if diff_val > 0 else "-"
                formatted_diff = abs(diff_val)
                if field in COST_FIELDS:
                    impact[field] = f"{sign}${formatted_diff:,.2f} (${new_val:,.2f})"
                elif field.endswith("tc_count") or field == "total_tc" or field == "tc_count":
                    impact[field] = f"{sign}{int(round(formatted_diff))} ({int(round(new_val))})"
                elif "hc" in field:
                    impact[field] = f"{sign}{formatted_diff:.2f} ({new_val:.2f})"
                else:
                    impact[field] = f"{sign}{formatted_diff:.2f} ({new_val:.2f})"

        meta = KNOB_METADATA.get(knob, {"name": knob, "description": ""})
        suggestions.append({
            "name": meta["name"],
            "description": meta["description"],
            "proposed_action": {
                "parameter": knob,
                "current_value": float(cur_effective_val),
                "new_value": float(solved_val),
            },
            "impact": impact,
            "side_effects_count": side_effects_count,
        })

    # 4. Ranking Logic
    # Sort primarily by side_effects_count (ascending), and secondarily by knob type
    # (overrides have priority over core inputs like tc_count or duration_in_days).
    def sorting_key(sugg: dict) -> tuple[int, int]:
        param = sugg["proposed_action"]["parameter"]
        side_effects = sugg["side_effects_count"]
        # Overrides = 0 priority, core inputs = 1 priority
        priority = 1 if param in {"tc_count", "duration_in_days"} else 0
        return side_effects, priority

    suggestions = sorted(suggestions, key=sorting_key)

    # Assign ranks
    for rank_idx, sugg in enumerate(suggestions, 1):
        sugg["rank"] = rank_idx

    warning = None
    if not suggestions:
        friendly_field_name = target_field.replace("_", " ").title()
        warning = (
            f"We couldn't find any combinations of settings to adjust to reach your target of "
            f"${target_value:,.2f} for '{friendly_field_name}'. The target value may be out of "
            f"realistic bounds, or the adjustments needed exceed system constraints (e.g. allocation caps)."
        )

    return {
        "target_field": target_field,
        "target_value": target_value,
        "current_value": current_value,
        "suggestions": suggestions,
        "warning": warning,
    }


async def solve_multi_goal_seek(
    db: AsyncSession,
    budget: Budget,
    targets: dict[str, float],
    adjustable_knobs: list[str] | None = None
) -> dict[str, Any]:
    """
    Solves for multiple target values on their respective calculated columns using
    SciPy's SLSQP optimizer.
    """
    from scipy.optimize import minimize
    import numpy as np

    rates = await fetch_rate_cards(db)

    # If knobs is not specified, use all adjustable parameters
    knobs = adjustable_knobs or ADJUSTABLE_KNOBS

    # 1. Capture current baseline values from the budget row
    baseline_tc_count = budget.tc_count
    baseline_duration = budget.duration_in_days

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
    # Keep only active overrides
    baseline_overrides = {k: v for k, v in baseline_overrides.items() if v is not None}

    # Helper function to run calculate_budget with test values
    def evaluate_combination(knob_values: dict[str, float]) -> dict[str, Any]:
        tc = knob_values.get("tc_count", baseline_tc_count)
        dur = knob_values.get("duration_in_days", baseline_duration)
        
        overrides = baseline_overrides.copy()
        for k, v in knob_values.items():
            if k not in ("tc_count", "duration_in_days"):
                overrides[k] = v
        return calculate_budget(tc_count=tc, duration_in_days=dur, rates=rates, overrides=overrides)

    # 2. Objective function: normalized sum of squared errors
    def objective(x):
        knob_vals = dict(zip(knobs, x))
        try:
            res = evaluate_combination(knob_vals)
        except ValueError:
            # Handle invalid values gracefully by returning large penalty
            return 1e12

        loss = 0.0
        for field, target_val in targets.items():
            actual_val = res.get(field, 0.0)
            norm = max(1.0, abs(target_val))
            loss += ((actual_val - target_val) / norm) ** 2
        return loss

    # 3. Formulate bounds and initial guess
    x0 = []
    bounds = []
    for knob in knobs:
        if knob == "tc_count":
            val = baseline_tc_count
        elif knob == "duration_in_days":
            val = baseline_duration
        else:
            val = baseline_overrides.get(knob)
            if val is None:
                global_key = OVERRIDE_TO_GLOBAL_KEY[knob]
                val = rates[global_key]
        x0.append(val)
        
        low, high = get_search_bounds(knob, val)
        bounds.append((low, high))

    # 4. Run minimize using SLSQP
    res_opt = minimize(
        objective,
        np.array(x0),
        method="Nelder-Mead",
        bounds=bounds,
        options={"maxfev": 1000}
    )

    # Map the solved array back to knobs
    solved_vals = dict(zip(knobs, res_opt.x.tolist()))
    
    # Calculate final result
    final_res = evaluate_combination(solved_vals)

    resulting_values = {field: final_res.get(field, 0.0) for field in targets}

    # 5. Check if we hit targets exactly or had to compromise
    had_compromise = False
    for field, target_val in targets.items():
        actual_val = resulting_values[field]
        if abs(actual_val - target_val) > 0.05:
            had_compromise = True
            break

    warning = None
    if had_compromise:
        warning = (
            "The solver could not meet all targets exactly due to parameter bounds "
            "or mathematical conflicts. The results represent the closest possible compromise."
        )

    # Return adjustments
    adjustments = {k: float(v) for k, v in solved_vals.items()}

    return {
        "targets": targets,
        "resulting_values": resulting_values,
        "adjustments": adjustments,
        "warning": warning,
    }

