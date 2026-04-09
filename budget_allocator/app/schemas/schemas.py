"""
app/schemas/schemas.py
----------------------
Pydantic V2 schemas for request validation and response serialization.

Convention
----------
* `<Model>Create`  — payload accepted on POST endpoints
* `<Model>Update`  — payload accepted on PATCH endpoints (all fields Optional)
* `<Model>Out`     — response shape returned to the client
* `<Model>InDB`    — internal shape that may include sensitive fields (not exposed)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ===========================================================================
# Shared config
# ===========================================================================

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Auth / Token schemas
# ===========================================================================

class TokenResponse(BaseModel):
    """Returned after a successful login."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8)
    totp_code: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=6,
        description="6-digit TOTP code — required once MFA is enabled for the user",
    )


class SetupAccountRequest(BaseModel):
    """
    Used on the one-time provisioning link: POST /auth/setup.
    The setup JWT is passed as a Bearer token, not in the body.
    """
    new_password: str = Field(..., min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        errors: list[str] = []
        if not any(c.isupper() for c in v):
            errors.append("at least one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("at least one digit")
        if not any(c in "!@#$%^&*()-_=+[]{}|;:',.<>?/" for c in v):
            errors.append("at least one special character")
        if errors:
            raise ValueError(f"Password must contain: {', '.join(errors)}")
        return v


class SetupAccountResponse(BaseModel):
    """Response after account setup — includes TOTP provisioning URI."""
    message: str
    totp_provisioning_uri: str
    username: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=12)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        errors: list[str] = []
        if not any(c.isupper() for c in v):
            errors.append("at least one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("at least one digit")
        if not any(c in "!@#$%^&*()-_=+[]{}|;:',.<>?/" for c in v):
            errors.append("at least one special character")
        if errors:
            raise ValueError(f"Password must contain: {', '.join(errors)}")
        return v


# ===========================================================================
# User schemas
# ===========================================================================

class UserProvisionRequest(BaseModel):
    """Admin hits POST /users/provision with this payload."""
    username: str = Field(..., min_length=3, max_length=64)
    is_admin: bool = False


class UserProvisionResponse(BaseModel):
    """Returned to the Admin — contains the single-use setup link token."""
    user_id: uuid.UUID
    username: str
    setup_token: str
    message: str = (
        "Share this token with the user over a secure channel. "
        "It is single-use and expires per the configured TTL."
    )


class UserOut(_Base):
    id: uuid.UUID
    username: str
    is_admin: bool
    is_active: bool
    requires_password_change: bool
    totp_enabled: bool = Field(
        default=False,
        description="True when the user has completed MFA setup",
    )
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _set_totp_enabled(cls, data: object) -> object:
        # `data` may be a SQLAlchemy ORM object (from_attributes=True)
        if hasattr(data, "totp_secret"):
            # We don't expose the secret; just indicate whether it's set
            object.__setattr__(data, "totp_enabled", data.totp_secret is not None)
        return data


class ResetLinkResponse(BaseModel):
    setup_token: str
    message: str = "Provide this token to the user to complete their password reset."


# ===========================================================================
# Project schemas
# ===========================================================================

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=256)
    status: str = "ACTIVE"


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=256)
    status: Optional[str] = None


class ProjectOut(_Base):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# SubDivision schemas
# ===========================================================================

class SubDivisionCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=2, max_length=256)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: str = "PLANNED"

    @model_validator(mode="after")
    def _validate_dates(self) -> "SubDivisionCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class SubDivisionUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None


class SubDivisionOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    status: str


# ===========================================================================
# Budget schemas
# ===========================================================================

class BudgetCreate(BaseModel):
    """
    Only the *manual inputs* are accepted from the client.
    All calculated fields are derived server-side by CalculationService.
    """
    sub_division_id: uuid.UUID
    tc_count: int = Field(..., gt=0, description="Total Test Case count (manual input)")
    duration_in_days: int = Field(..., gt=0, description="Engagement duration in working days")


class BudgetOut(_Base):
    id: uuid.UUID
    sub_division_id: uuid.UUID

    # Inputs
    tc_count: int
    duration_in_days: int

    # Calculated
    manual_tc_count: Optional[float]
    automation_tc_count: Optional[float]
    adhoc_request: Optional[float]
    total_tc: Optional[float]
    duration_wks: Optional[float]
    manual_hc: Optional[float]
    automation_hc: Optional[float]
    manual_hc_cost: Optional[float]
    automation_hc_cost: Optional[float]
    lead_cost: Optional[float]
    sqpm_cost_boise: Optional[float]
    pl_cost: Optional[float]
    per_wqe_cost: Optional[float]
    asqpm_cost: Optional[float]
    lab_tech_manager_cost: Optional[float]
    project_manager_cost: Optional[float]
    total_budget: Optional[float]

    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Rate Card schemas
# ===========================================================================

class RateCardCreate(BaseModel):
    key_name: str = Field(..., min_length=2, max_length=128)
    value: float = Field(..., description="The numeric multiplier or rate value")
    description: Optional[str] = Field(default=None, max_length=512)


class RateCardUpdate(BaseModel):
    value: float
    description: Optional[str] = None


class RateCardOut(_Base):
    id: int
    key_name: str
    value: float
    description: Optional[str]
    updated_at: datetime


# ===========================================================================
# Audit Log schemas
# ===========================================================================

class AuditLogOut(_Base):
    id: int
    entity_type: str
    entity_id: str
    action: str
    old_value: Optional[dict]
    new_value: Optional[dict]
    user_id: Optional[uuid.UUID]
    timestamp: datetime


# ===========================================================================
# Notification schemas
# ===========================================================================

class NotificationOut(_Base):
    id: int
    user_id: uuid.UUID
    message: str
    is_read: bool
    created_at: datetime


class NotificationMarkRead(BaseModel):
    notification_ids: list[int] = Field(..., min_length=1)
