"""
app/api/dependencies/filters.py
-------------------------------
Filtering dependencies for GET endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Query
from pydantic import BaseModel, Field


class AuditFilterParams(BaseModel):
    actor_id: uuid.UUID | None = Field(default=None, description="Filter by user who performed the action")
    action_type: str | None = Field(default=None, description="Filter by exact action type e.g. CREATE")
    start_date: datetime | None = Field(default=None, description="Filter records after this timestamp")
    end_date: datetime | None = Field(default=None, description="Filter records before this timestamp")


class BudgetFilterParams(BaseModel):
    min_total_cost: float | None = Field(default=None, description="Minimum total budget cost")
    max_total_cost: float | None = Field(default=None, description="Maximum total budget cost")
    min_headcount: float | None = Field(default=None, description="Minimum manual headcount")
    has_overrides: bool | None = Field(default=None, description="Only show budgets with non-null overrides")
    is_locked: bool | None = Field(default=None, description="Filter by locked status")


class WorkflowFilterParams(BaseModel):
    status: str | None = Field(default=None, description="Filter by status")
    business_unit_id: uuid.UUID | None = Field(default=None, description="Filter by business unit ID")


def get_audit_filters(
    actor_id: uuid.UUID | None = Query(None, description="Filter by actor ID"),
    action_type: str | None = Query(None, description="Filter by action type"),
    start_date: datetime | None = Query(None, description="Start date limit"),
    end_date: datetime | None = Query(None, description="End date limit"),
) -> AuditFilterParams:
    return AuditFilterParams(
        actor_id=actor_id,
        action_type=action_type,
        start_date=start_date,
        end_date=end_date,
    )


def get_budget_filters(
    min_total_cost: float | None = Query(None, description="Min total budget"),
    max_total_cost: float | None = Query(None, description="Max total budget"),
    min_headcount: float | None = Query(None, description="Min manual headcount"),
    has_overrides: bool | None = Query(None, description="Budgets with rate overrides"),
    is_locked: bool | None = Query(None, description="Locked status"),
) -> BudgetFilterParams:
    return BudgetFilterParams(
        min_total_cost=min_total_cost,
        max_total_cost=max_total_cost,
        min_headcount=min_headcount,
        has_overrides=has_overrides,
        is_locked=is_locked,
    )


def get_workflow_filters(
    status: str | None = Query(None, description="Filter teams/status"),
    business_unit_id: uuid.UUID | None = Query(None, description="Filter from top level BU ID"),
) -> WorkflowFilterParams:
    return WorkflowFilterParams(
        status=status,
        business_unit_id=business_unit_id,
    )
