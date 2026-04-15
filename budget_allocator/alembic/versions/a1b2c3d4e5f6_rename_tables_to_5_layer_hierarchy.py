"""rename tables and columns to 5-layer hierarchy

Revision ID: a1b2c3d4e5f6
Revises: 5b28fb0a0dee
Create Date: 2026-04-16 02:00:00.000000

Renames (zero data loss):
  projects        -> families
  sub_divisions   -> teams        (col project_id -> family_id)
  test_runs       -> runs         (col sub_division_id -> team_id)
  budgets.test_run_id -> budgets.run_id

Also:
  - Renames constraint/index names to match the new tables.
  - Renames postgres enum types project_status -> family_status,
    sub_division_status -> team_status.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5b28fb0a0dee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 0. Rename postgres enum types (done early, before tables reference them)
    # ------------------------------------------------------------------
    conn.execute(sa.text("ALTER TYPE project_status RENAME TO family_status"))
    conn.execute(sa.text("ALTER TYPE sub_division_status RENAME TO team_status"))

    # ------------------------------------------------------------------
    # 1. Rename the main tables
    # ------------------------------------------------------------------
    op.rename_table("projects", "families")
    op.rename_table("sub_divisions", "teams")
    op.rename_table("test_runs", "runs")

    # ------------------------------------------------------------------
    # 2. Rename FK columns inside the renamed tables
    # ------------------------------------------------------------------
    op.alter_column("teams", "project_id", new_column_name="family_id")
    op.alter_column("runs", "sub_division_id", new_column_name="team_id")
    op.alter_column("budgets", "test_run_id", new_column_name="run_id")

    # ------------------------------------------------------------------
    # 3. Drop old FK constraints (they still reference old table/col names)
    # ------------------------------------------------------------------
    # teams.family_id -> families.id
    op.drop_constraint("sub_divisions_project_id_fkey", "teams", type_="foreignkey")
    # runs.team_id -> teams.id
    op.drop_constraint("test_runs_sub_division_id_fkey", "runs", type_="foreignkey")
    # budgets.run_id -> runs.id
    op.drop_constraint("budgets_test_run_id_fkey", "budgets", type_="foreignkey")

    # ------------------------------------------------------------------
    # 4. Drop old unique/index constraints on renamed tables
    # ------------------------------------------------------------------
    # projects -> families unique constraint
    op.drop_constraint("uix_projects_bu_name", "families", type_="unique")

    # teams partial index (references old column name)
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_subdivision_project_name"))

    # old index names on families
    op.drop_index("ix_projects_name", table_name="families")
    op.drop_index("ix_projects_business_unit", table_name="families")

    # old index on teams (old column name)
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_sub_divisions_project_id"))

    # old index on runs (old column name)
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_test_runs_sub_division_id"))

    # old index on budgets (old column name)
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_budgets_test_run_id"))

    # ------------------------------------------------------------------
    # 5. Recreate FK constraints pointing to renamed tables
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "teams_family_id_fkey", "teams", "families", ["family_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "runs_team_id_fkey", "runs", "teams", ["team_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "budgets_run_id_fkey", "budgets", "runs", ["run_id"], ["id"], ondelete="CASCADE"
    )

    # ------------------------------------------------------------------
    # 6. Recreate indexes with new names
    # ------------------------------------------------------------------
    op.create_unique_constraint("uix_families_bu_name", "families", ["business_unit", "name"])
    op.create_index("ix_families_name", "families", ["name"])
    op.create_index("ix_families_business_unit", "families", ["business_unit"])

    op.create_index("ix_teams_family_id", "teams", ["family_id"])

    # Recreate partial unique index for teams with new column name
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX uq_team_family_name ON teams (family_id, name) "
        "WHERE is_deleted = false"
    ))

    op.create_index("ix_runs_team_id", "runs", ["team_id"])

    # budgets.run_id unique index
    op.create_index("ix_budgets_run_id", "budgets", ["run_id"], unique=True)

    # ------------------------------------------------------------------
    # 7. Add missing columns to runs table (status, start_date, end_date)
    #    These were present in the original TestRun model but may be missing
    # ------------------------------------------------------------------
    # Check and add status column if not present
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='runs' AND column_name='status'
            ) THEN
                ALTER TABLE runs ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE';
            END IF;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Drop new indexes and constraints
    op.drop_constraint("uix_families_bu_name", "families", type_="unique")
    op.drop_constraint("teams_family_id_fkey", "teams", type_="foreignkey")
    op.drop_constraint("runs_team_id_fkey", "runs", type_="foreignkey")
    op.drop_constraint("budgets_run_id_fkey", "budgets", type_="foreignkey")

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_budgets_run_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_runs_team_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_team_family_name"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_teams_family_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_families_business_unit"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_families_name"))

    # Rename columns back
    op.alter_column("budgets", "run_id", new_column_name="test_run_id")
    op.alter_column("runs", "team_id", new_column_name="sub_division_id")
    op.alter_column("teams", "family_id", new_column_name="project_id")

    # Rename tables back
    op.rename_table("runs", "test_runs")
    op.rename_table("teams", "sub_divisions")
    op.rename_table("families", "projects")

    # Restore old FK constraints
    op.create_foreign_key(
        "sub_divisions_project_id_fkey", "sub_divisions", "projects",
        ["project_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "test_runs_sub_division_id_fkey", "test_runs", "sub_divisions",
        ["sub_division_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "budgets_test_run_id_fkey", "budgets", "test_runs",
        ["test_run_id"], ["id"], ondelete="CASCADE"
    )

    # Restore old indexes
    op.create_unique_constraint("uix_projects_bu_name", "projects", ["business_unit", "name"])
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_business_unit", "projects", ["business_unit"])
    op.create_index("ix_sub_divisions_project_id", "sub_divisions", ["project_id"])
    op.create_index("ix_test_runs_sub_division_id", "test_runs", ["sub_division_id"])
    op.create_index("ix_budgets_test_run_id", "budgets", ["test_run_id"], unique=True)

    conn.execute(sa.text(
        "CREATE UNIQUE INDEX uq_subdivision_project_name ON sub_divisions (project_id, name) "
        "WHERE is_deleted = false"
    ))

    # Rename enum types back
    conn.execute(sa.text("ALTER TYPE family_status RENAME TO project_status"))
    conn.execute(sa.text("ALTER TYPE team_status RENAME TO sub_division_status"))
