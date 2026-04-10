"""Add PASSWORD_RESET audit event

Revision ID: a680436f2106
Revises: a4557317174f
Create Date: 2026-04-10 00:52:27.773037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a680436f2106'
down_revision: Union[str, None] = 'a4557317174f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Manual SQL to add a value to the PostgreSQL custom enum type
    # Postgres doesn't allow ALTER TYPE ... ADD VALUE inside a transaction block,
    # so we use commit() to break out of the session transaction if needed,
    # but Alembic's PostgresqlImpl usually handles this with execute().
    op.execute("ALTER TYPE auth_event_type ADD VALUE 'PASSWORD_RESET'")


def downgrade() -> None:
    # Removing a value from an Enum in Postgres is complex and requires
    # recreating the type. Given this is a leaf-node addition, we just skip.
    pass
