"""
app/services/calculation_service.py
------------------------------------
The Excel-replacement calculation engine.

Design philosophy
-----------------
* Formulas are HARDCODED in Python — no formula strings, no eval(), no user-
  supplied expressions.  Only the *multiplier values* are dynamic (sourced from
  the RateCards table) so Admin can adjust rates without a code deploy.
* All math operates on plain Python floats — no Pandas, no NumPy needed.
* The function is intentionally pure (no side-effects): it receives raw inputs
  and rate-card values and returns a fully-populated dict ready to be written
  to the Budgets table.

Excel formula cross-reference (Demo.xlsx)
------------------------------------------
Row  | Label                      | Formula
-----|----------------------------|-------------------------
  5  | Manual TC Count            | =C2*0.8
  6  | Automation TC Count        | =C2*0.2
  7  | Adhoc Request              | =C2*0.2
  8  | Total TC                   | =SUM(C5:C7)
  9  | Duration in Days           | (manual input)
 10  | Duration Wks               | =C9/5   (working days / week)
 11  | Manual HC                  | =SUM(C5,C7)/C10/3.5
 12  | Automation HC              | =C6/5
 13  | Manual HC Cost             | =C11*D13*C10      (HC * 40hr * ratecard)
 14  | Automation HC Cost         | =C12*D14*C10
 15  | Lead Cost                  | =D15*C10
 16  | SQPM Cost of Boise 70%     | =D16*C10*0.7
 17  | PL-50%                     | =D17*C10*0.5
 18  | Per WQE - 40%              | =6*C10*D18*0.4
 19  | aSQPM - 80%                | =D19*C10*0.8
 20  | Lab Tech & Manager - 40%   | =D20*2*C10*0.4
 21  | Project Manager - 40%      | =C10*D21*0.4
 23  | Total Budget               | =SUM(C13:C22)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RateCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required rate-card keys  (must be seeded into the DB on first run)
# ---------------------------------------------------------------------------

REQUIRED_RATE_KEYS: list[str] = [
    "manual_tc_multiplier",       # 0.8
    "automation_tc_multiplier",   # 0.2
    "adhoc_request_multiplier",   # 0.2
    "working_days_per_week",      # 5.0
    "hrs_per_wk_per_hc",          # 40.0
    "manual_hc_divisor",          # 3.5
    "automation_hc_divisor",      # 5.0
    "hc_rate_card",               # 2.00  ($/hr equivalent)
    "sqpm_boise_pct",             # 0.7
    "pl_pct",                     # 0.5
    "per_wqe_pct",                # 0.4   (note: uses factor of 6 in formula)
    "asqpm_pct",                  # 0.8
    "lab_tech_manager_pct",       # 0.4   (note: uses factor of 2 in formula)
    "project_manager_pct",        # 0.4
]


async def fetch_rate_cards(db: AsyncSession) -> dict[str, float]:
    """
    Load all RateCard rows from the database and return as {key_name: value}.

    Raises
    ------
    ValueError  — if any required key is missing from the table.
    """
    result = await db.execute(select(RateCard))
    rate_cards = result.scalars().all()
    rates = {rc.key_name: rc.value for rc in rate_cards}

    missing = [k for k in REQUIRED_RATE_KEYS if k not in rates]
    if missing:
        raise ValueError(
            f"RateCards table is missing required keys: {missing}. "
            "Please seed the database with default rate cards."
        )

    return rates


def calculate_budget(
    *,
    tc_count: int,
    duration_in_days: int,
    rates: dict[str, float],
) -> dict[str, Any]:
    """
    Execute the full budget calculation using hardcoded formulas and dynamic rates.

    Parameters
    ----------
    tc_count          : Total test case count (manual manager input).
    duration_in_days  : Engagement duration in working days (manual input).
    rates             : Dict of rate-card values fetched from the DB.

    Returns
    -------
    Dict whose keys match the column names in the `budgets` table (excluding
    `id`, `sub_division_id`, `tc_count`, `duration_in_days`).
    """

    r = rates  # shorthand

    # ------------------------------------------------------------------
    # Step 1: TC decomposition
    # ------------------------------------------------------------------
    manual_tc: float = tc_count * r["manual_tc_multiplier"]          # =C2*0.8
    automation_tc: float = tc_count * r["automation_tc_multiplier"]  # =C2*0.2
    adhoc: float = tc_count * r["adhoc_request_multiplier"]          # =C2*0.2
    total_tc: float = manual_tc + automation_tc + adhoc               # =SUM(C5:C7)

    # ------------------------------------------------------------------
    # Step 2: Duration
    # ------------------------------------------------------------------
    duration_wks: float = duration_in_days / r["working_days_per_week"]  # =C9/5

    # ------------------------------------------------------------------
    # Step 3: Headcount (HC)
    # ------------------------------------------------------------------
    # Manual HC: =SUM(manual_tc, total_tc) / (duration_wks * manual_hc_divisor)
    manual_hc: float = (manual_tc + total_tc) / (duration_wks * r["manual_hc_divisor"])

    # Automation HC: =automation_tc / automation_hc_divisor
    automation_hc: float = automation_tc / r["automation_hc_divisor"]

    # ------------------------------------------------------------------
    # Step 4: Cost lines  (all: HC_count * hrs_per_week * rate * duration_wks)
    # ------------------------------------------------------------------
    hc_rate = r["hc_rate_card"]
    hrs = r["hrs_per_wk_per_hc"]

    # Row 13: Manual HC Cost  = manual_hc * 40hr * rate * duration_wks
    manual_hc_cost: float = manual_hc * hrs * hc_rate * duration_wks

    # Row 14: Automation HC Cost
    automation_hc_cost: float = automation_hc * hrs * hc_rate * duration_wks

    # Row 15: Lead Cost  = 1 lead * 40hr * rate * duration_wks
    lead_cost: float = 1.0 * hrs * hc_rate * duration_wks

    # Row 16: SQPM Cost of Boise 70%  = hc_rate * duration_wks * 0.7 * hrs
    sqpm_cost_boise: float = hrs * hc_rate * duration_wks * r["sqpm_boise_pct"]

    # Row 17: PL 50%
    pl_cost: float = hrs * hc_rate * duration_wks * r["pl_pct"]

    # Row 18: Per WQE 40%  — note Excel uses factor of 6 WQE resources
    per_wqe_cost: float = 6.0 * hrs * hc_rate * duration_wks * r["per_wqe_pct"]

    # Row 19: aSQPM 80%
    asqpm_cost: float = hrs * hc_rate * duration_wks * r["asqpm_pct"]

    # Row 20: Lab Tech & Manager 40%  — note Excel uses factor of 2 resources
    lab_tech_manager_cost: float = 2.0 * hrs * hc_rate * duration_wks * r["lab_tech_manager_pct"]

    # Row 21: Project Manager 40%
    project_manager_cost: float = hrs * hc_rate * duration_wks * r["project_manager_pct"]

    # ------------------------------------------------------------------
    # Step 5: Total Budget  (sum of all cost rows 13-21)
    # ------------------------------------------------------------------
    total_budget: float = (
        manual_hc_cost
        + automation_hc_cost
        + lead_cost
        + sqpm_cost_boise
        + pl_cost
        + per_wqe_cost
        + asqpm_cost
        + lab_tech_manager_cost
        + project_manager_cost
    )

    logger.debug(
        "Budget calculation complete: tc=%s days=%s total=%.2f",
        tc_count,
        duration_in_days,
        total_budget,
    )

    return {
        "manual_tc_count": round(manual_tc, 4),
        "automation_tc_count": round(automation_tc, 4),
        "adhoc_request": round(adhoc, 4),
        "total_tc": round(total_tc, 4),
        "duration_wks": round(duration_wks, 4),
        "manual_hc": round(manual_hc, 4),
        "automation_hc": round(automation_hc, 4),
        "manual_hc_cost": round(manual_hc_cost, 2),
        "automation_hc_cost": round(automation_hc_cost, 2),
        "lead_cost": round(lead_cost, 2),
        "sqpm_cost_boise": round(sqpm_cost_boise, 2),
        "pl_cost": round(pl_cost, 2),
        "per_wqe_cost": round(per_wqe_cost, 2),
        "asqpm_cost": round(asqpm_cost, 2),
        "lab_tech_manager_cost": round(lab_tech_manager_cost, 2),
        "project_manager_cost": round(project_manager_cost, 2),
        "total_budget": round(total_budget, 2),
    }


async def compute_and_get_budget_fields(
    *,
    tc_count: int,
    duration_in_days: int,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Convenience coroutine: fetch rates then calculate.
    Use this from the router/service layer.
    """
    rates = await fetch_rate_cards(db)
    return calculate_budget(tc_count=tc_count, duration_in_days=duration_in_days, rates=rates)
