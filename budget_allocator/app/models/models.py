"""
app/models/models.py
--------------------
Complete SQLAlchemy 2.0 ORM model definitions using the new `Mapped` /
`mapped_column` API.

Design notes
~~~~~~~~~~~~
* Every table inherits from `Base` (defined in `app.core.database`).
* All primary keys use `uuid.UUID` to avoid sequential-ID enumeration attacks.
* `relationship()` back-populates are explicit to satisfy the async lazy-loading
  restriction — callers must use `selectinload` / `joinedload` instead of
  attribute access without an active session.
* The `Budget` table is intentionally *flat* (all calculated fields stored as
  columns) to make SQL aggregation and audit diffs easy to read.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ===========================================================================
# Enumerations
# ===========================================================================

class AuthEventType(str, enum.Enum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET = "PASSWORD_RESET"      # self-service forgot-password via TOTP
    MFA_ENABLED = "MFA_ENABLED"
    TOKEN_INVALIDATED = "TOKEN_INVALIDATED"


class FamilyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

# Keep alias for backward compat with any running alembic migrations
ProjectStatus = FamilyStatus


class TeamStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

# Alias
SubDivisionStatus = TeamStatus


class AuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class AdminActionType(str, enum.Enum):
    """Explicit admin intent actions — recorded regardless of ORM events."""
    USER_PROVISION          = "USER_PROVISION"           # Admin created a new user
    USER_UPDATE             = "USER_UPDATE"               # Admin updated user profile/role
    USER_DELETE             = "USER_DELETE"               # Admin soft-deleted a user
    USER_ACTIVATE           = "USER_ACTIVATE"             # Admin activated a user
    USER_DEACTIVATE         = "USER_DEACTIVATE"           # Admin deactivated a user
    USER_PASSWORD_RESET     = "USER_PASSWORD_RESET"       # Admin issued a password reset link


# ===========================================================================
# Users
# ===========================================================================

class User(Base):
    """
    Represents a human actor in the system.

    * `is_admin` — grants access to admin-only endpoints (rate-card mutation,
      user provisioning, password reset generation).
    * `requires_password_change` — set True after provisioning / admin reset;
      the frontend should redirect the user to the password-reset flow.
    * `totp_secret` — base32 secret stored per-user; NULL until MFA is set up.
    * `token_version` — incremented on password reset / forced logout so that
      all issued JWTs for this user become immediately invalid (no denylist needed).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )
    username: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    hashed_password: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    requires_password_change: Mapped[bool] = mapped_column(
        Boolean,
        default=True,   # True by default — forces password setup on first login
        nullable=False,
    )
    totp_secret: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default=None,
    )
    # Incrementing this integer invalidates ALL previously issued JWTs for
    # this user without maintaining a token denylist in Redis or the DB.
    token_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    auth_logs: Mapped[list["AuthLog"]] = relationship(
        "AuthLog",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="actor"
    )
    admin_action_logs: Mapped[list["AdminActionLog"]] = relationship(
        "AdminActionLog",
        back_populates="actor",
        foreign_keys="AdminActionLog.actor_id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id!s:.8} username={self.username!r}>"


# ===========================================================================
# Auth Logs
# ===========================================================================

class AuthLog(Base):
    """
    Immutable record of every authentication event for compliance / forensics.
    """

    __tablename__ = "auth_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[AuthEventType] = mapped_column(
        SAEnum(AuthEventType, name="auth_event_type", create_type=True),
        nullable=False,
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Denormalized username for quick log readability",
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="auth_logs")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuthLog id={self.id} event={self.event_type.value}>"


# ===========================================================================
# Families  ➜  Teams  ➜  Runs  ➜  Budgets
# ===========================================================================

class BusinessUnit(Base):
    """
    Layer 1 of the hierarchy: a Business Unit.
    e.g. "CPE", "ISB", "HIPS"
    """
    __tablename__ = "business_units"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Relationships
    families: Mapped[list["Family"]] = relationship(
        "Family",
        back_populates="business_unit",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BusinessUnit id={self.id!s:.8} name={self.name!r}>"


class Family(Base):
    """
    Layer 2 of the hierarchy: a Product Family within a Business Unit.
    e.g. BU="CPE", Family="INKJET"
    """

    __tablename__ = "families"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "name", name="uix_families_bu_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[FamilyStatus] = mapped_column(
        SAEnum(FamilyStatus, name="family_status", create_type=True),
        default=FamilyStatus.ACTIVE,
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Relationships
    business_unit: Mapped["BusinessUnit"] = relationship(
        "BusinessUnit",
        back_populates="families"
    )
    teams: Mapped[list["Team"]] = relationship(
        "Team",
        back_populates="family",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Family id={self.id!s:.8} name={self.name!r}>"


# Backward-compat alias so any code still using Project doesn't crash immediately
Project = Family


class Team(Base):
    """
    Layer 3: A Team within a Family.
    Each Team contains multiple Runs.
    """

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[TeamStatus] = mapped_column(
        SAEnum(TeamStatus, name="team_status", create_type=True),
        default=TeamStatus.PLANNED,
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "uq_team_family_name",
            "family_id",
            "name",
            unique=True,
            postgresql_where=(is_deleted == False),  # noqa: E712
        ),
    )

    # Relationships
    family: Mapped["Family"] = relationship(
        "Family",
        back_populates="teams"
    )
    runs: Mapped[list["Run"]] = relationship(
        "Run",
        back_populates="team",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Team id={self.id!s:.8} name={self.name!r}>"


# Backward-compat alias
SubDivision = Team


class Run(Base):
    """
    Layer 4: A specific test Run within a Team.
    Each Run has exactly one Budget record.
    """

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False, index=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "uq_run_team_name",
            "team_id",
            "name",
            unique=True,
            postgresql_where=(is_deleted == False),  # noqa: E712
        ),
    )

    # Relationships
    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="runs"
    )
    budget: Mapped[Optional["Budget"]] = relationship(
        "Budget",
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Run id={self.id!s:.8} name={self.name!r}>"


# Backward-compat alias
TestRun = Run


class Budget(Base):
    """
    Flat budget record for one TestRun.

    Column naming mirrors the Excel sheet columns exactly so that the
    CalculationService output maps 1-to-1 and auditors can cross-reference.

    Manual Input columns
    --------------------
    * `tc_count`          — raw TC count entered by manager
    * `duration_in_days`  — engagement duration entered by manager

    Calculated columns (populated by CalculationService, NOT the client)
    ------------------------------------------------------------------
    See calculation_service.py for the exact formulas.  All multipliers are
    sourced live from the RateCards table so Admins can change rates without a
    code deploy.

    Formula reference (from Excel Demo.xlsx)
    -----------------------------------------
    manual_tc_count      = tc_count * 0.8          (rate: manual_tc_multiplier)
    automation_tc_count  = tc_count * 0.2          (rate: automation_tc_multiplier)
    adhoc_request        = tc_count * 0.2          (rate: adhoc_request_multiplier)
    total_tc             = manual_tc + automation_tc + adhoc
    duration_wks         = duration_in_days / 5    (approx — working days)
    manual_hc            = SUM(manual_tc, total_tc) / (duration_wks * 3.5)
    automation_hc        = automation_tc / 5
    manual_hc_cost       = manual_hc  * duration_wks * hc_rate_card   (HC*40hr*ratecard)
    automation_hc_cost   = automation_hc * duration_wks * hc_rate_card
    lead_cost            = duration_wks * hc_rate_card
    total_budget         = SUM(manual_hc_cost .. lead_cost + all SQPM/PL/etc costs)
    """

    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,      # Enforce one Budget per Run at DB level
        index=True,
    )

    # ---- Manual Inputs (provided by the manager) ----------------------------
    tc_count: Mapped[float] = mapped_column(Float, nullable=False)
    duration_in_days: Mapped[float] = mapped_column(Float, nullable=False)

    # ---- Calculated Fields (written exclusively by CalculationService) ------
    manual_tc_count: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_tc_count: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adhoc_request: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_tc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_wks: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_hc: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    automation_hc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lead_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    direct_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    indirect_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ---- Additional cost lines (rows 16-21 from Demo.xlsx) ------------------
    sqpm_cost_boise: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # row 16: SQPM Cost of Boise 70%
    pl_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)           # row 17: PL-50%
    per_wqe_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)      # row 18: Per WQE - 40%
    asqpm_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)        # row 19: aSQPM - 80%
    lab_tech_manager_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # row 20: Lab Tech & Manager - 40%
    project_manager_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # row 21: Project Manager - 40%

    total_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)

    # ---- Per-Budget Rate Overrides (NULL = use global RateCard value) --------
    manual_tc_multiplier_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_tc_multiplier_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adhoc_request_multiplier_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    working_days_per_week_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hrs_per_wk_per_hc_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_hc_divisor_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_hc_divisor_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    manual_hourly_rate_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_hourly_rate_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    asqpm_hourly_rate_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lead_hourly_rate_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pm_hourly_rate_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    sqpm_boise_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pl_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    per_wqe_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    asqpm_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lab_tech_manager_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    project_manager_pct_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Relationships
    run: Mapped["Run"] = relationship(
        "Run",
        back_populates="budget"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Budget id={self.id!s:.8} "
            f"total={self.total_budget} "
            f"run={self.run_id!s:.8}>"
        )


# ===========================================================================
# Rate Cards  (Admin-configurable multipliers)
# ===========================================================================

class RateCard(Base):
    """
    Admin-controlled key/value store for all multipliers and rate constants.

    This is the "Hybrid" part of the architecture: the *formulas* live in
    Python (calculation_service.py), but the *values* live here so finance
    teams can adjust rates without touching code.

    Example rows
    ------------
    key_name="manual_tc_multiplier"    value=0.8
    key_name="automation_tc_multiplier" value=0.2
    key_name="adhoc_request_multiplier" value=0.2
    key_name="hc_rate_card"            value=2.00   (USD/hr equivalent)
    key_name="working_days_per_week"   value=5.0
    key_name="hrs_per_wk_per_hc"      value=40.0
    key_name="manual_hc_divisor"      value=3.5    (denominator in manual HC formula)
    key_name="automation_hc_divisor"  value=5.0
    key_name="sqpm_boise_pct"         value=0.7
    key_name="pl_pct"                 value=0.5
    key_name="per_wqe_pct"            value=0.4
    key_name="asqpm_pct"              value=0.8
    key_name="lab_tech_manager_pct"   value=0.4
    key_name="project_manager_pct"    value=0.4
    """

    __tablename__ = "rate_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_name: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RateCard key={self.key_name!r} value={self.value}>"


# ===========================================================================
# Company Holidays  (Used for working day math)
# ===========================================================================

class CompanyHoliday(Base):
    """
    Tracks company observing holidays to calculate accurate
    business delivery / resource headcount math.
    """
    __tablename__ = "company_holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_units.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    business_unit: Mapped[Optional["BusinessUnit"]] = relationship("BusinessUnit")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CompanyHoliday id={self.id} date={self.holiday_date}>"


# ===========================================================================
# Audit Logs
# ===========================================================================

class AuditLog(Base):
    """
    Append-only audit trail for all CRUD events on critical entities.

    Populated automatically by SQLAlchemy event listeners defined in
    `app.services.audit_logger` — developers should NEVER write to this
    table directly from router code.

    `old_value` / `new_value` are stored as JSON blobs so that any model's
    state can be captured without schema changes.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="SQLAlchemy model class name, e.g. 'Budget', 'RateCard'",
    )
    change_reason: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        comment="Mandatory comment for modifications",
    )
    entity_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="String-serialised primary key of the affected row",
    )
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, name="audit_action", create_type=True),
        nullable=False,
    )
    old_value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Nullable: system-generated events (e.g. scheduler) have no human actor
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Denormalized username for quick log readability",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    actor: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="audit_logs"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} "
            f"entity={self.entity_type}:{self.entity_id} "
            f"action={self.action.value}>"
        )


# ===========================================================================
# Consumed TOTP Codes  (replay-attack prevention)
# ===========================================================================

class ConsumedTOTPCode(Base):
    """
    Short-lived record of every TOTP code that has been successfully verified.

    Before accepting a login with a TOTP code, the auth layer checks this
    table.  If the code is found (and not yet expired) the login is rejected
    as a replay attack.

    Rows are pruned opportunistically at login time by ``crud_totp.purge_expired_codes``.
    The ``expires_at`` index makes the cleanup query efficient.
    """

    __tablename__ = "consumed_totp_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,   # Fast cleanup and lookup by expiry
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ConsumedTOTPCode user={self.user_id!s:.8} code={self.code}>"


# ===========================================================================
# Admin Action Logs
# ===========================================================================

class AdminActionLog(Base):
    """
    Human-readable record of every deliberate admin management action.

    Unlike `audit_logs` (which captures raw ORM diffs), this table captures
    *intent*: who decided to do what to whom, and what changed as a result.
    It answers compliance questions like:
      "Who granted admin rights to alice, and when?"
      "Which admin issued the last password-reset link for bob?"

    Design: append-only, never update or delete rows.
    """

    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Who performed the action
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_name: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Denormalized admin username at time of action",
    )

    # What action was taken
    action: Mapped[AdminActionType] = mapped_column(
        SAEnum(AdminActionType, name="admin_action_type", create_type=True),
        nullable=False,
        index=True,
    )

    # Who/what was affected
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="UUID of the affected user (or entity)",
    )
    target_name: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Denormalized target username at time of action",
    )

    # Structured diff / detail
    detail: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Structured details e.g. {old_role: false, new_role: true}",
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    actor: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="admin_action_logs",
        foreign_keys=[actor_id],
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AdminActionLog id={self.id} "
            f"actor={self.actor_name!r} "
            f"action={self.action.value} "
            f"target={self.target_name!r}>"
        )


# ===========================================================================
# Notifications
# ===========================================================================

class Notification(Base):
    """
    In-app notifications polled by the frontend "Notification Bell".

    Created by:
    * The nightly APScheduler job (deadline proximity alerts)
    * Any future system event that doesn't warrant a full audit entry
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="notifications"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification id={self.id} user={self.user_id!s:.8} read={self.is_read}>"
