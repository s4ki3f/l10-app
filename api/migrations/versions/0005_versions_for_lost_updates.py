"""Bonus: lost-update protection for scores and Issue priority.

Each score and Issue carries a version that every write bumps. A client that sends the version it saw
gets 409 if someone else saved first, instead of silently overwriting their change.

Revision ID: 0005_versions_for_lost_updates
Revises: 0004_rating_write_time
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_versions_for_lost_updates"
down_revision: Union[str, None] = "0004_rating_write_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("scores", "issues"):
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
        op.create_check_constraint(f"{table}_version_positive", table, "version >= 1")


def downgrade() -> None:
    for table in ("scores", "issues"):
        op.drop_constraint(f"{table}_version_positive", table, type_="check")
        op.drop_column(table, "version")
