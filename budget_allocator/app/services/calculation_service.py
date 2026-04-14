"""
app/services/calculation_service.py
------------------------------------
The Excel-replacement calculation engine.

Design philosophy
-----------------
* Formulas are HARDCODED in Python — no formula strings, no eval(), no user-
  supplied expressions.  Only the *multiplier values* are dynamic (sourced from
  the RateCards table) so Admin can adjust rates without a code deploy.
* All intermediate math uses ``decimal.Decimal`` for precision (Fix #14).
  IEEE-754 float accumulates rounding errors in multi-step financial
  calculations; Decimal avoids this.  Values are converted back to ``float``
  only at the boundary where SQLAlchemy writes them to the database.
* Zero-divisor guard: the function raises ``ValueError`` early if any rate-card
  key that is used as a denominator is zero (Fix #4).

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
import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import crud_rate_card
from app.models.models import RateCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required rate-card keys  (must be seeded into the DB on first run)
# ---------------------------------------------------------------------------

REQUIRED_RATE_KEYS: list[str] = [
    "manual_tc_multiplier",       # 0.8
    "automation_tc_multiplier",   # 0.2
    "adhoc_request_multiplier",   # 0.2
    "working_days_per_week",      # 5.0  ← divisor
    "hrs_per_wk_per_hc",          # 40.0
    "manual_hc_divisor",          # 3.5  ← divisor
    "automation_hc_divisor",      # 5.0  ← divisor
    "hc_rate_card",               # 2.00  ($/hr equivalent)
    "sqpm_boise_pct",             # 0.7
    "pl_pct",                     # 0.5
    "per_wqe_pct",                # 0.4   (note: uses factor of 6 in formula)
    "asqpm_pct",                  # 0.8
    "lab_tech_manager_pct",       # 0.4   (note: uses factor of 2 in formula)
    "project_manager_pct",        # 0.4
]

# Keys used as denominators — must never be zero (Fix #4)
_DIVISOR_KEYS: frozenset[str] = frozenset({
    "working_days_per_week",
    "manual_hc_divisor",
    "automation_hc_divisor",
})


async def fetch_rate_cards(db: AsyncSession) -> dict[str, float]:
    """
    Load all RateCard rows from the database and return as {key_name: value}.

    Raises
    ------
    ValueError  — if any required key is missing from the table.
    ValueError  — if any divisor key has a value of zero (Fix #4).
    """
    rate_cards = await crud_rate_card.get_all_rate_cards(db)
    rates = {rc.key_name: rc.value for rc in rate_cards}

    missing = [k for k in REQUIRED_RATE_KEYS if k not in rates]
    if missing:
        raise ValueError(
            f"RateCards table is missing required keys: {missing}. "
            "Please seed the database with default rate cards."
        )

    # Fix #4: Explicitly guard divisor keys — a zero value here causes
    # ZeroDivisionError for every subsequent budget calculation.
    zero_divisors = [k for k in _DIVISOR_KEYS if rates.get(k, 1) == 0]
    if zero_divisors:
        raise ValueError(
            f"RateCard keys {zero_divisors} are used as divisors and must not "
            "be zero.  Please update them via PATCH /admin/rate-cards."
        )

    return rates


def _D(value: int | float) -> Decimal:
    """Convert a number to Decimal using its string representation to avoid
    IEEE-754 representation noise (e.g. 0.2 → Decimal('0.2'), not
    Decimal('0.20000000000000001...')).
    """
    return Decimal(str(value))


def calculate_budget(
    *,
    tc_count: int,
    duration_in_days: int,
    rates: dict[str, float],
    overrides: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """
    Execute the full budget calculation using hardcoded formulas and dynamic
    rates.  Per-budget overrides take priority over global rate-card values.

    All intermediate arithmetic uses ``decimal.Decimal`` (Fix #14) to prevent
    the accumulation of IEEE-754 float rounding errors in multi-step financial
    calculations.  The output dict contains Python ``float`` values so
    SQLAlchemy can write them directly to ``Float`` columns.

    Parameters
    ----------
    tc_count          : Total test case count (manual manager input).
    duration_in_days  : Engagement duration in working days (manual input).
    rates             : Dict of rate-card values fetched from the DB.
    overrides         : Optional dict of per-budget override values.  A key
                        whose value is ``None`` is treated as "no override".

    Returns
    -------
    Dict whose keys match the column names in the ``budgets`` table (excluding
    ``id``, ``sub_division_id``, ``tc_count``, ``duration_in_days``).
    """
    _overrides: dict[str, float | None] = overrides or {}

    # Bug #4 fix: guard against zero-valued divisor overrides, same as fetch_rate_cards
    _DIVISOR_OVERRIDE_KEYS = {
        "working_days_per_week_override",
        "manual_hc_divisor_override",
        "automation_hc_divisor_override",
    }
    for _key in _DIVISOR_OVERRIDE_KEYS:
        _val = _overrides.get(_key)
        if _val is not None and _val == 0:
            raise ValueError(
                f"Override '{_key}' is used as a divisor and cannot be zero."
            )

    def _rate(global_key: str, override_key: str) -> Decimal:
        """
        Return the effective rate for a given key.
        Uses the per-budget override when it is explicitly provided (non-None),
        otherwise falls back to the global RateCard value.
        """
        ov = _overrides.get(override_key)
        return _D(ov) if ov is not None else _D(rates[global_key])

    tc = _D(tc_count)
    dur = _D(duration_in_days)

    # ------------------------------------------------------------------
    # Step 1: TC decomposition
    # ------------------------------------------------------------------
    manual_tc: Decimal = _D(math.ceil(tc * _rate("manual_tc_multiplier", "manual_tc_multiplier_override")))
    automation_tc: Decimal = _D(math.ceil(tc * _rate("automation_tc_multiplier", "automation_tc_multiplier_override")))
    adhoc: Decimal = _D(math.ceil(tc * _rate("adhoc_request_multiplier", "adhoc_request_multiplier_override")))
    total_tc: Decimal = manual_tc + automation_tc + adhoc         # =SUM(C5:C7)

    # ------------------------------------------------------------------
    # Step 2: Duration
    # ------------------------------------------------------------------
    duration_wks: Decimal = dur / _rate("working_days_per_week", "working_days_per_week_override")

    # ------------------------------------------------------------------
    # Step 3: Headcount (HC)
    # ------------------------------------------------------------------
    # Manual HC: =SUM(manual_tc, total_tc) / (duration_wks * manual_hc_divisor)
    manual_hc: Decimal = _D(math.ceil((manual_tc + total_tc) / (duration_wks * _rate("manual_hc_divisor", "manual_hc_divisor_override"))))

    # Automation HC: =automation_tc / automation_hc_divisor
    automation_hc: Decimal = _D(math.ceil(automation_tc / _rate("automation_hc_divisor", "automation_hc_divisor_override")))

    # ------------------------------------------------------------------
    # Step 4: Cost lines  (all: HC_count * hrs_per_week * rate * duration_wks)
    # ------------------------------------------------------------------
    hc_rate = _rate("hc_rate_card", "hc_rate_card_override")
    hrs = _rate("hrs_per_wk_per_hc", "hrs_per_wk_per_hc_override")

    # Row 13: Manual HC Cost  = manual_hc * 40hr * rate * duration_wks
    manual_hc_cost: Decimal = manual_hc * hrs * hc_rate * duration_wks

    # Row 14: Automation HC Cost
    automation_hc_cost: Decimal = automation_hc * hrs * hc_rate * duration_wks

    # Row 15: Lead Cost  = 1 lead * 40hr * rate * duration_wks
    lead_cost: Decimal = _D(1) * hrs * hc_rate * duration_wks

    # Row 16: SQPM Cost of Boise 70%  = hc_rate * duration_wks * 0.7 * hrs
    sqpm_cost_boise: Decimal = hrs * hc_rate * duration_wks * _rate("sqpm_boise_pct", "sqpm_boise_pct_override")

    # Row 17: PL 50%
    pl_cost: Decimal = hrs * hc_rate * duration_wks * _rate("pl_pct", "pl_pct_override")

    # Row 18: Per WQE 40%  — note Excel uses factor of 6 WQE resources
    per_wqe_cost: Decimal = _D(6) * hrs * hc_rate * duration_wks * _rate("per_wqe_pct", "per_wqe_pct_override")

    # Row 19: aSQPM 80%
    asqpm_cost: Decimal = hrs * hc_rate * duration_wks * _rate("asqpm_pct", "asqpm_pct_override")

    # Row 20: Lab Tech & Manager 40%  — note Excel uses factor of 2 resources
    lab_tech_manager_cost: Decimal = _D(2) * hrs * hc_rate * duration_wks * _rate("lab_tech_manager_pct", "lab_tech_manager_pct_override")

    # Row 21: Project Manager 40%
    project_manager_cost: Decimal = hrs * hc_rate * duration_wks * _rate("project_manager_pct", "project_manager_pct_override")

    # ------------------------------------------------------------------
    # Step 5: Total Budget  (sum of all cost rows 13-21)
    # ------------------------------------------------------------------
    total_budget: Decimal = (
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

    # Round at the output boundary only, then convert to float for SQLAlchemy.
    def _r4(d: Decimal) -> float:
        return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    def _r2(d: Decimal) -> float:
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "tc_count":             tc_count,
        "duration_in_days":     duration_in_days,
        "manual_tc_count":      int(manual_tc),
        "automation_tc_count":  int(automation_tc),
        "adhoc_request":        int(adhoc),
        "total_tc":             int(total_tc),
        "duration_wks":         _r4(duration_wks),
        "manual_hc":            int(manual_hc),
        "automation_hc":        int(automation_hc),
        "manual_hc_cost":       _r2(manual_hc_cost),
        "automation_hc_cost":   _r2(automation_hc_cost),
        "lead_cost":            _r2(lead_cost),
        "sqpm_cost_boise":      _r2(sqpm_cost_boise),
        "pl_cost":              _r2(pl_cost),
        "per_wqe_cost":         _r2(per_wqe_cost),
        "asqpm_cost":           _r2(asqpm_cost),
        "lab_tech_manager_cost": _r2(lab_tech_manager_cost),
        "project_manager_cost": _r2(project_manager_cost),
        "total_budget":         _r2(total_budget),
        **_overrides,
    }


async def compute_and_get_budget_fields(
    *,
    tc_count: int,
    duration_in_days: int,
    db: AsyncSession,
    overrides: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """
    Convenience coroutine: fetch rates then calculate.
    Use this from the router/service layer.

    Parameters
    ----------
    overrides : Optional dict of per-budget override values keyed by the
                ``*_override`` column name (e.g. ``hc_rate_card_override``).
                A value of ``None`` means "use global rate".
    """
    rates = await fetch_rate_cards(db)
    return calculate_budget(
        tc_count=tc_count,
        duration_in_days=duration_in_days,
        rates=rates,
        overrides=overrides,
    )
