"""
app/api/v1/endpoints/analytics.py
---------------------------------
HTTP Controller for aggregate company-wide financial analytics.

Route summary
~~~~~~~~~~~~~
  GET  /analytics/global-summary   — return company-wide budget totals
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.database import get_db
from app.models.models import Budget, Team, User
from app.schemas.schemas import BudgetSummaryOut

from app.crud import crud_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.get("/global-summary", response_model=BudgetSummaryOut)
async def get_global_summary(
    business_unit: str | None = None,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Returns a unified global roll-up of all active budgets in the system.
    Excludes any budgets associated with soft-deleted projects, sub-divisions, runs, or budgets.
    Optionally filter by business_unit.
    """
    summary_data = await crud_analytics.get_global_budget_summary(db, business_unit=business_unit)
    if not summary_data:
        return BudgetSummaryOut(
            tc_count=0,
            duration_wks=0.0,
            manual_hc=0,
            automation_hc=0,
            manual_hc_cost=0.0,
            automation_hc_cost=0.0,
            lead_cost=0.0,
            sqpm_cost_boise=0.0,
            pl_cost=0.0,
            per_wqe_cost=0.0,
            asqpm_cost=0.0,
            lab_tech_manager_cost=0.0,
            project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)


@router.get("/family-summary/{family_id}", response_model=BudgetSummaryOut)
async def get_family_summary(
    family_id: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Returns a unified roll-up of all active budgets for a specific Family.
    """
    summary_data = await crud_analytics.get_family_budget_summary(db, family_id)
    if not summary_data:
        return BudgetSummaryOut(
            tc_count=0,
            duration_wks=0.0,
            manual_hc=0,
            automation_hc=0,
            manual_hc_cost=0.0,
            automation_hc_cost=0.0,
            lead_cost=0.0,
            sqpm_cost_boise=0.0,
            pl_cost=0.0,
            per_wqe_cost=0.0,
            asqpm_cost=0.0,
            lab_tech_manager_cost=0.0,
            project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)


@router.get("/team-summary/{team_id}", response_model=BudgetSummaryOut)
async def get_team_summary(
    team_id: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Returns a unified roll-up of all active budgets for a specific Team.
    """
    summary_data = await crud_analytics.get_team_budget_summary(db, team_id)
    if not summary_data:
        return BudgetSummaryOut(
            tc_count=0, duration_wks=0.0, manual_hc=0, automation_hc=0,
            manual_hc_cost=0.0, automation_hc_cost=0.0, lead_cost=0.0,
            sqpm_cost_boise=0.0, pl_cost=0.0, per_wqe_cost=0.0,
            asqpm_cost=0.0, lab_tech_manager_cost=0.0, project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)


@router.get("/run-summary/{run_id}", response_model=BudgetSummaryOut)
async def get_run_summary(
    run_id: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Returns the budget summary for a specific active Run.
    """
    summary_data = await crud_analytics.get_run_budget_summary(db, run_id)
    if not summary_data:
        return BudgetSummaryOut(
            tc_count=0, duration_wks=0.0, manual_hc=0, automation_hc=0,
            manual_hc_cost=0.0, automation_hc_cost=0.0, lead_cost=0.0,
            sqpm_cost_boise=0.0, pl_cost=0.0, per_wqe_cost=0.0,
            asqpm_cost=0.0, lab_tech_manager_cost=0.0, project_manager_cost=0.0,
            total_budget=0.0,
        )

    return BudgetSummaryOut(**summary_data)
