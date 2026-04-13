"""
app/api/v1/endpoints/meta.py
-----------------------------
Lightweight metadata endpoints for frontend bootstrapping.

Route summary
~~~~~~~~~~~~~
  GET  /meta/enums   — return all dropdown enum values for the frontend
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_current_user
from app.models.models import User
from app.models.models import AuditAction, ProjectStatus, SubDivisionStatus

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/enums")
async def get_enums(
    _: User = Depends(get_current_user),
) -> dict:
    """
    Return all enum value lists needed by the frontend for dropdowns.

    This endpoint allows the UI to stay in sync with the backend's valid
    states without hard-coding them client-side.
    """
    return {
        "project_statuses": [s.value for s in ProjectStatus],
        "subdivision_statuses": [s.value for s in SubDivisionStatus],
        "audit_actions": [a.value for a in AuditAction],
    }
