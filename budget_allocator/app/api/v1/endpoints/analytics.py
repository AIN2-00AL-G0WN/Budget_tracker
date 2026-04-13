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
from app.models.models import Budget, SubDivision, User
from app.schemas.schemas import BudgetSummaryOut

from app.crud import crud_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.get("/global-summary", response_model=BudgetSummaryOut)
async def get_global_summary(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BudgetSummaryOut:
    """
    Returns a unified global roll-up of all active budgets in the system.
    Excludes any budgets associated with soft-deleted projects, sub-divisions, or budgets.
    """
    summary_data = await crud_analytics.get_global_budget_summary(db)
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
