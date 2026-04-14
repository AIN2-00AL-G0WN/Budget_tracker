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
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ===========================================================================
# Shared config
# ===========================================================================

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response envelope used across list endpoints."""
    items: list[T]
    total: int
    limit: int
    offset: int


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





class ForgotPasswordRequest(BaseModel):
    """
    Self-service password reset using TOTP as proof-of-identity.
    No current password required — the authenticator code replaces it.
    """
    username: str = Field(..., min_length=3, max_length=64)
    new_password: str = Field(..., min_length=12, max_length=128)
    confirm_password: str = Field(..., min_length=12, max_length=128)
    totp_code: str = Field(..., min_length=6, max_length=6, description="6-digit code from your Authenticator app")

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

    @model_validator(mode="after")
    def _passwords_match(self) -> "ForgotPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password do not match")
        return self


class ForgotPasswordResponse(BaseModel):
    """Returned after a successful self-service password reset."""
    message: str = (
        "Password has been reset successfully. All existing sessions have been invalidated."
    )


class ChangePasswordRequest(BaseModel):
    """
    Authenticated password change for logged-in users.
    Requires the current password as proof of intent.
    """
    current_password: str = Field(..., min_length=8)
    new_password: str = Field(..., min_length=12, max_length=128)
    confirm_password: str = Field(..., min_length=12, max_length=128)
    totp_code: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=6,
        description="6-digit TOTP code — required if MFA is enabled for the account",
    )

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

    @model_validator(mode="after")
    def _passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password do not match")
        return self


class ChangePasswordResponse(BaseModel):
    """Returned after a successful password change."""
    message: str = (
        "Password has been changed successfully. All other active sessions "
        "have been invalidated."
    )
    totp_provisioning_uri: Optional[str] = Field(
        default=None,
        description=(
            "Present only when MFA was not previously configured. "
            "Scan this URI with your Authenticator app to enable MFA."
        ),
    )


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
    business_unit: str = Field(..., min_length=2, max_length=128)
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def _valid_project_status(cls, v: str) -> str:
        valid = {"ACTIVE", "ON_HOLD", "COMPLETED", "CANCELLED"}
        if v not in valid:
            raise ValueError(f"Invalid project status '{v}'. Must be one of: {sorted(valid)}")
        return v


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=256)
    business_unit: Optional[str] = Field(default=None, min_length=2, max_length=128)
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _valid_project_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        valid = {"ACTIVE", "ON_HOLD", "COMPLETED", "CANCELLED"}
        if v not in valid:
            raise ValueError(f"Invalid project status '{v}'. Must be one of: {sorted(valid)}")
        return v


class ProjectOut(_Base):
    id: uuid.UUID
    name: str
    business_unit: str
    status: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Team schemas (formerly SubDivision)
# ===========================================================================

class TeamCreate(BaseModel):
    """
    ``project_id`` is intentionally absent — it is taken from the URL path
    parameter in the router to avoid body/path discrepancies (Fix #7).
    """
    name: str = Field(..., min_length=2, max_length=256)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: str = "PLANNED"

    #_VALID_SD_STATUSES class variable removed — was dead code (Bug #8 fix)

    @field_validator("status")
    @classmethod
    def _valid_subdivision_status(cls, v: str) -> str:
        valid = {"PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
        if v not in valid:
            raise ValueError(
                f"Invalid subdivision status '{v}'. "
                f"Must be one of: {sorted(valid)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_dates(self) -> "TeamCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _valid_subdivision_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        valid = {"PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
        if v not in valid:
            raise ValueError(
                f"Invalid subdivision status '{v}'. "
                f"Must be one of: {sorted(valid)}"
            )
        return v


class TeamOut(_Base):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    is_deleted: bool


# ===========================================================================
# TestRun schemas
# ===========================================================================

class TestRunCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=256)


class TestRunUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=256)


class TestRunOut(_Base):
    id: uuid.UUID
    sub_division_id: uuid.UUID
    name: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ===========================================================================
# Budget schemas
# ===========================================================================

class BudgetCreate(BaseModel):
    """
    Only the *manual inputs* are accepted from the client.
    All calculated fields are derived server-side by CalculationService.

    Rate overrides (all optional)
    ------------------------------
    If provided, each override replaces the corresponding global RateCard value
    for this budget's calculation only.  Omit or pass ``null`` to use the
    admin-configured global rate.
    """
    test_run_id: Optional[uuid.UUID] = Field(default=None, description="FK to parent TestRun")
    tc_count: Optional[float] = Field(default=None, gt=0, description="Total Test Case count (manual input)")
    duration_in_days: Optional[float] = Field(default=None, gt=0, description="Engagement duration in working days")

    # Per-budget rate overrides — all optional
    manual_tc_multiplier_override: Optional[float] = Field(default=None, gt=0)
    automation_tc_multiplier_override: Optional[float] = Field(default=None, gt=0)
    adhoc_request_multiplier_override: Optional[float] = Field(default=None, gt=0)
    working_days_per_week_override: Optional[float] = Field(default=None, gt=0)
    hrs_per_wk_per_hc_override: Optional[float] = Field(default=None, gt=0)
    manual_hc_divisor_override: Optional[float] = Field(default=None, gt=0)
    automation_hc_divisor_override: Optional[float] = Field(default=None, gt=0)
    manual_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    automation_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    asqpm_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    lead_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    pm_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    sqpm_boise_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    pl_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    per_wqe_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    asqpm_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    lab_tech_manager_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    project_manager_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)


class BudgetUpdate(BaseModel):
    """
    Schema used exclusively by PATCH /budgets/{id}.

    Deliberately omits ``test_run_id``: a budget cannot be re-linked to a
    different TestRun after creation.  Including it in the PATCH body would
    be misleading.
    """
    test_run_id: uuid.UUID = Field(..., description="Bind this budget to a TestRun")
    tc_count: float = Field(..., gt=0, description="Total Test Case count (manual input)")
    duration_in_days: float = Field(..., gt=0, description="Engagement duration in working days")

    # Per-budget rate overrides — all optional
    manual_tc_multiplier_override: Optional[float] = Field(default=None, gt=0)
    automation_tc_multiplier_override: Optional[float] = Field(default=None, gt=0)
    adhoc_request_multiplier_override: Optional[float] = Field(default=None, gt=0)
    working_days_per_week_override: Optional[float] = Field(default=None, gt=0)
    hrs_per_wk_per_hc_override: Optional[float] = Field(default=None, gt=0)
    manual_hc_divisor_override: Optional[float] = Field(default=None, gt=0)
    automation_hc_divisor_override: Optional[float] = Field(default=None, gt=0)
    manual_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    automation_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    asqpm_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    lead_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    pm_hourly_rate_override: Optional[float] = Field(default=None, gt=0)
    sqpm_boise_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    pl_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    per_wqe_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    asqpm_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    lab_tech_manager_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)
    project_manager_pct_override: Optional[float] = Field(default=None, ge=0, le=1.0)


class BudgetOut(_Base):
    id: uuid.UUID
    test_run_id: uuid.UUID

    # Inputs
    tc_count: float
    duration_in_days: float

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

    # Applied rate overrides (None = global rate was used)
    manual_tc_multiplier_override: Optional[float]
    automation_tc_multiplier_override: Optional[float]
    adhoc_request_multiplier_override: Optional[float]
    working_days_per_week_override: Optional[float]
    hrs_per_wk_per_hc_override: Optional[float]
    manual_hc_divisor_override: Optional[float]
    automation_hc_divisor_override: Optional[float]
    manual_hourly_rate_override: Optional[float]
    automation_hourly_rate_override: Optional[float]
    asqpm_hourly_rate_override: Optional[float]
    lead_hourly_rate_override: Optional[float]
    pm_hourly_rate_override: Optional[float]
    sqpm_boise_pct_override: Optional[float]
    pl_pct_override: Optional[float]
    per_wqe_pct_override: Optional[float]
    asqpm_pct_override: Optional[float]
    lab_tech_manager_pct_override: Optional[float]
    project_manager_pct_override: Optional[float]

    is_deleted: bool
    is_locked: bool
    created_at: datetime
    updated_at: datetime


class BudgetSummaryOut(BaseModel):
    tc_count: float
    duration_wks: float
    manual_hc: float
    automation_hc: float
    manual_hc_cost: float
    automation_hc_cost: float
    lead_cost: float
    sqpm_cost_boise: float
    pl_cost: float
    per_wqe_cost: float
    asqpm_cost: float
    lab_tech_manager_cost: float
    project_manager_cost: float
    total_budget: float


class FullSummarySnapshotOut(BudgetSummaryOut):
    """
    Complete copy of all numeric fields from the budget, for audit records.
    Includes rate overrides.
    """
    duration_in_days: float
    manual_tc_count: Optional[float]
    automation_tc_count: Optional[float]
    adhoc_request: Optional[float]
    total_tc: Optional[float]
    
    manual_tc_multiplier_override: Optional[float]
    automation_tc_multiplier_override: Optional[float]
    adhoc_request_multiplier_override: Optional[float]
    working_days_per_week_override: Optional[float]
    hrs_per_wk_per_hc_override: Optional[float]
    manual_hc_divisor_override: Optional[float]
    automation_hc_divisor_override: Optional[float]
    manual_hourly_rate_override: Optional[float]
    automation_hourly_rate_override: Optional[float]
    asqpm_hourly_rate_override: Optional[float]
    lead_hourly_rate_override: Optional[float]
    pm_hourly_rate_override: Optional[float]
    sqpm_boise_pct_override: Optional[float]
    pl_pct_override: Optional[float]
    per_wqe_pct_override: Optional[float]
    asqpm_pct_override: Optional[float]
    lab_tech_manager_pct_override: Optional[float]
    project_manager_pct_override: Optional[float]


# ===========================================================================
# Rate Card schemas
# ===========================================================================

# Keys whose value is used as a divisor in calculation_service.py.  Zero
# is never valid for these — it would cause a guaranteed ZeroDivisionError
# on every subsequent budget calculation (Fix #4).
_DIVISOR_RATE_KEYS: frozenset[str] = frozenset({
    "working_days_per_week",
    "manual_hc_divisor",
    "automation_hc_divisor",
})


class RateCardCreate(BaseModel):
    key_name: str = Field(..., min_length=2, max_length=128)
    value: float = Field(..., description="The numeric multiplier or rate value")
    description: Optional[str] = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _reject_zero_divisor(self) -> "RateCardCreate":
        if self.key_name in _DIVISOR_RATE_KEYS and self.value == 0:
            raise ValueError(
                f"'{self.key_name}' is used as a divisor in budget calculations "
                "and cannot be zero."
            )
        return self


class RateCardUpdate(BaseModel):
    key_name: Optional[str] = Field(default=None, min_length=2, max_length=128)
    value: Optional[float] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _reject_zero_divisor(self) -> "RateCardUpdate":
        if (
            self.key_name in _DIVISOR_RATE_KEYS
            and self.value is not None
            and self.value == 0
        ):
            raise ValueError(
                f"'{self.key_name}' is used as a divisor in budget calculations "
                "and cannot be zero."
            )
        return self


class RateCardOut(_Base):
    id: int
    key_name: str
    value: float
    description: Optional[str]
    updated_at: datetime


# ===========================================================================
# Company Holiday schemas
# ===========================================================================

class CompanyHolidayCreate(BaseModel):
    holiday_date: date
    description: str = Field(..., min_length=2, max_length=256)

class CompanyHolidayUpdate(BaseModel):
    holiday_date: Optional[date] = None
    description: Optional[str] = Field(default=None, min_length=2, max_length=256)

class CompanyHolidayOut(_Base):
    id: int
    holiday_date: date
    description: str
    is_deleted: bool

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
