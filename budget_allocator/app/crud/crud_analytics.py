"""
app/crud/crud_analytics.py
--------------------------
Data-Access Layer for Analytics and Rollups.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Project, SubDivision


async def get_global_budget_summary(db: AsyncSession) -> dict | None:
    """
    Returns aggregated budget stats across all sub-divisions for all active
    projects, returning a dictionary representation of BudgetSummaryOut.

    Filters out soft-deleted Budgets, SubDivisions, and Projects.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(Budget.tc_count), 0).label("tc_count"),
            func.coalesce(func.sum(Budget.duration_wks), 0).label("duration_wks"),
            func.coalesce(func.sum(Budget.manual_hc), 0).label("manual_hc"),
            func.coalesce(func.sum(Budget.automation_hc), 0).label("automation_hc"),
            func.coalesce(func.sum(Budget.manual_hc_cost), 0).label("manual_hc_cost"),
            func.coalesce(func.sum(Budget.automation_hc_cost), 0).label("automation_hc_cost"),
            func.coalesce(func.sum(Budget.lead_cost), 0).label("lead_cost"),
            func.coalesce(func.sum(Budget.sqpm_cost_boise), 0).label("sqpm_cost_boise"),
            func.coalesce(func.sum(Budget.pl_cost), 0).label("pl_cost"),
            func.coalesce(func.sum(Budget.per_wqe_cost), 0).label("per_wqe_cost"),
            func.coalesce(func.sum(Budget.asqpm_cost), 0).label("asqpm_cost"),
            func.coalesce(func.sum(Budget.lab_tech_manager_cost), 0).label("lab_tech_manager_cost"),
            func.coalesce(func.sum(Budget.project_manager_cost), 0).label("project_manager_cost"),
            func.coalesce(func.sum(Budget.total_budget), 0).label("total_budget"),
        )
        .join(SubDivision, Budget.sub_division_id == SubDivision.id)
        .join(Project, SubDivision.project_id == Project.id)
        .where(
            Project.is_deleted == False,  # noqa: E712
            SubDivision.is_deleted == False,  # noqa: E712
            Budget.is_deleted == False,  # noqa: E712
        )
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None
