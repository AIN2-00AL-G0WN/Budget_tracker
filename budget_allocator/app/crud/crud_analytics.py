"""
app/crud/crud_analytics.py
--------------------------
Data-Access Layer for Analytics and Rollups.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Family, Team, Run


async def get_global_budget_summary(db: AsyncSession, business_unit: str | None = None) -> dict | None:
    """
    Returns aggregated budget stats across all sub-divisions for all active
    projects, returning a dictionary representation of BudgetSummaryOut.

    Filters out soft-deleted Budgets, SubDivisions, and Projects.
    Optionally filters by business_unit.
    """
    from app.models.models import Run
    stmt = select(
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
    ).join(Run, Budget.run_id == Run.id) \
     .join(Team, Run.team_id == Team.id) \
     .join(Family, Team.family_id == Family.id) \
     .where(
         Family.is_deleted == False,
         Team.is_deleted == False,
         Run.is_deleted == False,
         Budget.is_deleted == False,
     )

    if business_unit:
        stmt = stmt.where(Family.business_unit == business_unit)

    result = await db.execute(stmt)
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_family_budget_summary(db: AsyncSession, family_id: str) -> dict | None:
    from app.models.models import Run
    stmt = select(
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
    ).join(Run, Budget.run_id == Run.id) \
     .join(Team, Run.team_id == Team.id) \
     .join(Family, Team.family_id == Family.id) \
     .where(
         Family.id == family_id,
         Family.is_deleted == False,
         Team.is_deleted == False,
         Run.is_deleted == False,
         Budget.is_deleted == False,
     )

    result = await db.execute(stmt)
    row = result.one_or_none()
    return dict(row._mapping) if row else None


# Backward-compat alias
get_project_budget_summary = get_family_budget_summary


async def get_team_budget_summary(db: AsyncSession, team_id: str) -> dict | None:
    from app.models.models import Run
    stmt = select(
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
    ).join(Run, Budget.run_id == Run.id) \
     .join(Team, Run.team_id == Team.id) \
     .where(
         Team.id == team_id,
         Team.is_deleted == False,
         Run.is_deleted == False,
         Budget.is_deleted == False,
     )

    result = await db.execute(stmt)
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_run_budget_summary(db: AsyncSession, run_id: str) -> dict | None:
    from app.models.models import Run
    stmt = select(
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
    ).join(Run, Budget.run_id == Run.id) \
     .where(
         Run.id == run_id,
         Run.is_deleted == False,
         Budget.is_deleted == False,
     )

    result = await db.execute(stmt)
    row = result.one_or_none()
    return dict(row._mapping) if row else None
