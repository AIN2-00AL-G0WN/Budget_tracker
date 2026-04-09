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
    MFA_ENABLED = "MFA_ENABLED"
    TOKEN_INVALIDATED = "TOKEN_INVALIDATED"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SubDivisionStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


# ===========================================================================
# Users
# ===========================================================================

class User(Base):
    """
    Represents a human actor in the system.

    * `is_admin` — grants access to admin-only endpoints (rate-card mutation,
      user provisioning, password reset generation).
    * `requires_password_change` — set True after provisioning / admin reset;
      the frontend should redirect the user to the change-password flow.
    * `totp_secret` — base32 secret stored per-user; NULL until MFA is set up.
    * `token_version` — incremented on password change / forced logout so that
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
        cascade="all, delete-orphan",
        lazy="noload",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="actor",
        lazy="noload",
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
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="auth_logs", lazy="noload")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuthLog id={self.id} event={self.event_type.value}>"


# ===========================================================================
# Projects  ➜  SubDivisions  ➜  Budgets
# ===========================================================================

class Project(Base):
    """
    Top-level grouping (e.g., "Walmart SKU Stage").
    A project contains multiple SubDivisions (e.g., "CPE/Release").
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status", create_type=True),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    sub_divisions: Mapped[list["SubDivision"]] = relationship(
        "SubDivision",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id!s:.8} name={self.name!r}>"


class SubDivision(Base):
    """
    A phase/stream inside a Project (e.g., "CPE/Release").
    Each SubDivision has exactly one Budget record.

    The `end_date` field is used by the nightly APScheduler job to generate
    deadline-proximity Notifications.
    """

    __tablename__ = "sub_divisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[SubDivisionStatus] = mapped_column(
        SAEnum(SubDivisionStatus, name="sub_division_status", create_type=True),
        default=SubDivisionStatus.PLANNED,
        nullable=False,
    )

    __table_args__ = (
        # A project cannot have two sub-divisions with identical names
        UniqueConstraint("project_id", "name", name="uq_subdivision_project_name"),
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="sub_divisions",
        lazy="noload",
    )
    budget: Mapped[Optional["Budget"]] = relationship(
        "Budget",
        back_populates="sub_division",
        uselist=False,            # One-to-one
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubDivision id={self.id!s:.8} name={self.name!r}>"


class Budget(Base):
    """
    Flat budget record for one SubDivision.

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
    sub_division_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sub_divisions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,      # Enforce one Budget per SubDivision at DB level
        index=True,
    )

    # ---- Manual Inputs (provided by the manager) ----------------------------
    tc_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_in_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # ---- Calculated Fields (written exclusively by CalculationService) ------
    manual_tc_count: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_tc_count: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adhoc_request: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_tc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_wks: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_hc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_hc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automation_hc_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lead_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ---- Additional cost lines (rows 16-21 from Demo.xlsx) ------------------
    sqpm_cost_boise: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # row 16: SQPM Cost of Boise 70%
    pl_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)           # row 17: PL-50%
    per_wqe_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)      # row 18: Per WQE - 40%
    asqpm_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)        # row 19: aSQPM - 80%
    lab_tech_manager_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # row 20: Lab Tech & Manager - 40%
    project_manager_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # row 21: Project Manager - 40%

    total_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    sub_division: Mapped["SubDivision"] = relationship(
        "SubDivision",
        back_populates="budget",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Budget id={self.id!s:.8} "
            f"total={self.total_budget} "
            f"sub_division={self.sub_division_id!s:.8}>"
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
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    actor: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="audit_logs",
        lazy="noload",
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
        back_populates="notifications",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification id={self.id} user={self.user_id!s:.8} read={self.is_read}>"
