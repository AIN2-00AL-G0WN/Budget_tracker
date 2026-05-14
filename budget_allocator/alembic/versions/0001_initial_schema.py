"""Initial schema — full clean baseline

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-14

Replaces all previous incremental migrations with a single authoritative
baseline that matches the current SQLAlchemy models exactly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE auth_event_type AS ENUM (
                'LOGIN_SUCCESS', 'LOGIN_FAILED', 'PASSWORD_CHANGED',
                'PASSWORD_RESET', 'MFA_ENABLED', 'TOKEN_INVALIDATED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE family_status AS ENUM (
                'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CANCELLED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE team_status AS ENUM (
                'PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE audit_action AS ENUM ('CREATE', 'UPDATE', 'DELETE');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE admin_action_type AS ENUM (
                'USER_PROVISION', 'USER_UPDATE', 'USER_DELETE',
                'USER_ACTIVATE', 'USER_DEACTIVATE', 'USER_PASSWORD_RESET'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_password_change", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"])

    # ── auth_logs ─────────────────────────────────────────────────────────────
    op.create_table(
        "auth_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Enum("LOGIN_SUCCESS", "LOGIN_FAILED", "PASSWORD_CHANGED",
                                        "PASSWORD_RESET", "MFA_ENABLED", "TOKEN_INVALIDATED",
                                        name="auth_event_type", create_type=False), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_auth_logs_user_id", "auth_logs", ["user_id"])
    op.create_index("ix_auth_logs_timestamp", "auth_logs", ["timestamp"])

    # ── business_units ────────────────────────────────────────────────────────
    op.create_table(
        "business_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_name", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_name", sa.String(64), nullable=True),
    )
    op.create_index("ix_business_units_name", "business_units", ["name"])

    # ── families ──────────────────────────────────────────────────────────────
    op.create_table(
        "families",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("business_unit_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("business_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "ON_HOLD", "COMPLETED", "CANCELLED",
                                    name="family_status", create_type=False),
                  nullable=False, server_default="ACTIVE"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_name", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_name", sa.String(64), nullable=True),
        sa.UniqueConstraint("business_unit_id", "name", name="uix_families_bu_name"),
    )
    op.create_index("ix_families_name", "families", ["name"])
    op.create_index("ix_families_business_unit_id", "families", ["business_unit_id"])

    # ── teams ─────────────────────────────────────────────────────────────────
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Enum("PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED",
                                    name="team_status", create_type=False),
                  nullable=False, server_default="PLANNED"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_name", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_name", sa.String(64), nullable=True),
    )
    op.create_index("ix_teams_family_id", "teams", ["family_id"])
    op.create_index("ix_teams_end_date", "teams", ["end_date"])
    op.execute(
        "CREATE UNIQUE INDEX uq_team_family_name ON teams (family_id, name) "
        "WHERE is_deleted = false"
    )

    # ── runs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_name", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_name", sa.String(64), nullable=True),
    )
    op.create_index("ix_runs_team_id", "runs", ["team_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_end_date", "runs", ["end_date"])
    op.execute(
        "CREATE UNIQUE INDEX uq_run_team_name ON runs (team_id, name) "
        "WHERE is_deleted = false AND status = 'ACTIVE'"
    )

    # ── budgets ───────────────────────────────────────────────────────────────
    op.create_table(
        "budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        # Manual inputs
        sa.Column("tc_count", sa.Float(), nullable=False),
        sa.Column("duration_in_days", sa.Float(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        # Calculated fields
        sa.Column("manual_tc_count", sa.Float(), nullable=True),
        sa.Column("automation_tc_count", sa.Float(), nullable=True),
        sa.Column("adhoc_request", sa.Float(), nullable=True),
        sa.Column("total_tc", sa.Float(), nullable=True),
        sa.Column("duration_wks", sa.Float(), nullable=True),
        sa.Column("manual_hc", sa.Float(), nullable=True),
        sa.Column("automation_hc", sa.Float(), nullable=True),
        sa.Column("manual_hc_cost", sa.Float(), nullable=True),
        sa.Column("automation_hc_cost", sa.Float(), nullable=True),
        sa.Column("lead_cost", sa.Float(), nullable=True),
        sa.Column("direct_hc_cost", sa.Float(), nullable=True),
        sa.Column("indirect_hc_cost", sa.Float(), nullable=True),
        sa.Column("sqpm_cost_boise", sa.Float(), nullable=True),
        sa.Column("pl_cost", sa.Float(), nullable=True),
        sa.Column("per_wqe_cost", sa.Float(), nullable=True),
        sa.Column("asqpm_cost", sa.Float(), nullable=True),
        sa.Column("lab_tech_manager_cost", sa.Float(), nullable=True),
        sa.Column("project_manager_cost", sa.Float(), nullable=True),
        sa.Column("total_budget", sa.Float(), nullable=True),
        # Per-budget overrides
        sa.Column("manual_tc_multiplier_override", sa.Float(), nullable=True),
        sa.Column("automation_tc_multiplier_override", sa.Float(), nullable=True),
        sa.Column("adhoc_request_multiplier_override", sa.Float(), nullable=True),
        sa.Column("working_days_per_week_override", sa.Float(), nullable=True),
        sa.Column("hrs_per_wk_per_hc_override", sa.Float(), nullable=True),
        sa.Column("manual_hc_divisor_override", sa.Float(), nullable=True),
        sa.Column("automation_hc_divisor_override", sa.Float(), nullable=True),
        sa.Column("manual_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("automation_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("asqpm_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("lead_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("pm_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("sqpm_boise_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("pl_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("per_wqe_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("lab_tech_manager_hourly_rate_override", sa.Float(), nullable=True),
        sa.Column("sqpm_boise_pct_override", sa.Float(), nullable=True),
        sa.Column("pl_pct_override", sa.Float(), nullable=True),
        sa.Column("per_wqe_pct_override", sa.Float(), nullable=True),
        sa.Column("asqpm_pct_override", sa.Float(), nullable=True),
        sa.Column("lab_tech_manager_pct_override", sa.Float(), nullable=True),
        sa.Column("project_manager_pct_override", sa.Float(), nullable=True),
        # Flags & metadata
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_name", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_name", sa.String(64), nullable=True),
    )
    op.create_index("ix_budgets_run_id", "budgets", ["run_id"])
    op.create_index("ix_budgets_manual_hc", "budgets", ["manual_hc"])
    op.create_index("ix_budgets_total_budget", "budgets", ["total_budget"])
    op.create_index("ix_budgets_is_locked", "budgets", ["is_locked"])
    op.create_index("ix_budgets_end_date", "budgets", ["end_date"])
    op.execute(
        "CREATE UNIQUE INDEX uq_budget_run_id_active ON budgets (run_id) "
        "WHERE is_deleted = false"
    )

    # ── rate_cards ────────────────────────────────────────────────────────────
    op.create_table(
        "rate_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_name", sa.String(128), unique=True, nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rate_cards_key_name", "rate_cards", ["key_name"])

    # ── company_holidays ──────────────────────────────────────────────────────
    op.create_table(
        "company_holidays",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("business_unit_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("business_units.id", ondelete="CASCADE"), nullable=True),
        sa.Column("description", sa.String(256), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_company_holidays_holiday_date", "company_holidays", ["holiday_date"])
    op.create_index("ix_company_holidays_business_unit_id", "company_holidays", ["business_unit_id"])

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("change_reason", sa.String(), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("action", sa.Enum("CREATE", "UPDATE", "DELETE",
                                    name="audit_action", create_type=False), nullable=False),
        sa.Column("old_value", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])

    # ── consumed_totp_codes ───────────────────────────────────────────────────
    op.create_table(
        "consumed_totp_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_consumed_totp_codes_user_id", "consumed_totp_codes", ["user_id"])
    op.create_index("ix_consumed_totp_codes_expires_at", "consumed_totp_codes", ["expires_at"])

    # ── admin_action_logs ─────────────────────────────────────────────────────
    op.create_table(
        "admin_action_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(64), nullable=True),
        sa.Column("action", sa.Enum("USER_PROVISION", "USER_UPDATE", "USER_DELETE",
                                    "USER_ACTIVATE", "USER_DEACTIVATE", "USER_PASSWORD_RESET",
                                    name="admin_action_type", create_type=False), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_name", sa.String(64), nullable=True),
        sa.Column("detail", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_action_logs_actor_id", "admin_action_logs", ["actor_id"])
    op.create_index("ix_admin_action_logs_action", "admin_action_logs", ["action"])
    op.create_index("ix_admin_action_logs_target_id", "admin_action_logs", ["target_id"])
    op.create_index("ix_admin_action_logs_timestamp", "admin_action_logs", ["timestamp"])

    # ── notifications ─────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("admin_action_logs")
    op.drop_table("consumed_totp_codes")
    op.drop_table("audit_logs")
    op.drop_table("company_holidays")
    op.drop_table("rate_cards")
    op.drop_table("budgets")
    op.drop_table("runs")
    op.drop_table("teams")
    op.drop_table("families")
    op.drop_table("business_units")
    op.drop_table("auth_logs")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS admin_action_type")
    op.execute("DROP TYPE IF EXISTS audit_action")
    op.execute("DROP TYPE IF EXISTS team_status")
    op.execute("DROP TYPE IF EXISTS family_status")
    op.execute("DROP TYPE IF EXISTS auth_event_type")
